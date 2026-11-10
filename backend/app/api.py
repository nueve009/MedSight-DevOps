"""
MedSight backend: receives OCR text from the mobile app and returns what to
read aloud. OCR and text-to-speech run on the phone; this runs the rest of the
pipeline (see pipeline.py).

Run locally:
    uvicorn app.api:app --reload   (from the backend/ folder)

Endpoints:
    GET  /health       liveness check for the cloud host
    POST /v2/identify  contract v2 (README.md): every product, scan rules, tiers
    POST /identify     contract v1, kept until every active app build uses v2
    /admin/...         answer cache review and fixes (admin.py), off unless ADMIN_ENABLED
    /admin/kb/...      the review console for the reviewed dataset (review.py), same switch

Before any model call, each identify request passes the API key check and
the rate limits (ratelimit.py): 429 with spoken text and Retry-After when a
device, address or the day's model budget is over its limit.

Every response carries an X-Request-Id header (the client's own, if it sent a
valid one), and every log line for that request includes the same ID.
"""
import logging
import secrets
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import admin, config, review, speech
from app.kb_source import KBRefresher, kb_conninfo, load_from_postgres
from app.llm_client import LLMUnavailableError
from app.logging_setup import RequestIdMiddleware, configure_logging, request_id_var
from app.pipeline import ScanRequest, identify, identify_scan
from app.ratelimit import Limiter, LLMBudgetExceeded, RateLimited, parse_pairs
from app.retrieval import KnowledgeBase
from app.scan_rules import Block, CaptureHints
from app.store import make_store
from app.utils import hash_device_id

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(config.LOG_LEVEL)
    config.ensure_dirs()
    # The cache and scan log: SQLite locally, Supabase on Railway (CACHE_BACKEND).
    app.state.cache = make_store()
    app.state.limiter = Limiter(app.state.cache)
    app.state.scan_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_SCANS)
    # The reviewed dataset: approved rows in Supabase (re-read in the background),
    # or data/kb.sqlite built by scripts/kb_build.py (KB_BACKEND).
    refresher = start_kb(app.state)
    log.info("startup", extra={"fields": {"llm_provider": config.LLM_PROVIDER, "kb_version": app.state.kb.version,
                                          "kb_products": len(app.state.kb.products),
                                          "cache_backend": config.CACHE_BACKEND, "kb_backend": config.KB_BACKEND}})
    yield
    if refresher:
        refresher.stop()
    app.state.cache.close()


def start_kb(state) -> Optional[KBRefresher]:
    """Put the reviewed dataset in state.kb; for Supabase, also start its refresher."""
    backend = config.KB_BACKEND
    if backend == "sqlite":
        state.kb = KnowledgeBase.load(config.KB_DB_PATH)
        return None
    if backend != "postgres":
        raise RuntimeError(f"KB_BACKEND must be 'sqlite' or 'postgres', not {backend!r}")
    conninfo = kb_conninfo()
    if not conninfo:
        # A configuration mistake, not an outage: refuse to start.
        raise RuntimeError("KB_BACKEND=postgres needs KB_DATABASE_URL or DATABASE_URL")
    state.kb = KnowledgeBase()
    refresher = KBRefresher(state, lambda: load_from_postgres(conninfo), config.KB_REFRESH_S)
    refresher.load_once()   # a failure is logged; scans run on the empty dataset until a reload works
    refresher.start()
    return refresher


class BodySizeLimitMiddleware:
    """Refuses request bodies over config.MAX_BODY_BYTES with 413, whether or
    not the client sent a Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        # Evidence photos are the one larger upload, and those routes need a signed-in reviewer.
        limit = (config.MAX_EVIDENCE_BODY_BYTES if path.startswith("/admin/kb/") and path.endswith("/evidence")
                 else config.MAX_BODY_BYTES)
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > limit:
                await JSONResponse({"detail": "Request body too large."}, status_code=413)(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI passes HTTPException through its body parsing unchanged.
                    raise HTTPException(status_code=413, detail="Request body too large.")
            return message

        await self.app(scope, limited_receive, send)


DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


class RouteGateMiddleware:
    """Answers 404 for the API docs and schema unless config.API_DOCS is on, and
    for every /admin path unless config.ADMIN_ENABLED is on (both read per
    request, so tests can switch them)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        hidden = ((path in DOCS_PATHS and not config.API_DOCS)
                  or ((path == "/admin" or path.startswith("/admin/")) and not config.ADMIN_ENABLED))
        if hidden:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        await self.app(scope, receive, send)


app = FastAPI(title="MedSight backend", lifespan=lifespan)
app.add_middleware(RouteGateMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
if config.REVIEW_ALLOWED_ORIGINS:   # the hosted review page; the app and the local console never need this
    app.add_middleware(CORSMiddleware, allow_origins=config.REVIEW_ALLOWED_ORIGINS,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])
app.add_middleware(RequestIdMiddleware)   # added last = outermost, so even a 413 gets an ID
app.include_router(admin.router)
app.include_router(review.router)


def _api_keys() -> dict[str, str]:
    """name -> key: MEDSIGHT_API_KEYS ("name=key,..."), plus MEDSIGHT_API_KEY as "default"."""
    keys = parse_pairs(config.MEDSIGHT_API_KEYS)
    if config.MEDSIGHT_API_KEY:
        keys.setdefault("default", config.MEDSIGHT_API_KEY)
    return keys


def require_api_key(request: Request, x_api_key: str = Header("")) -> None:
    keys = _api_keys()
    if not keys:
        # Fail closed: an unset key must not leave the LLM quota open to anyone.
        raise HTTPException(status_code=503, detail="Server API key is not configured.")
    matched = None
    for name, key in keys.items():   # every key is compared, so timing doesn't reveal which matched
        if secrets.compare_digest(x_api_key.encode(), key.encode()):
            matched = name
    if matched is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    request.state.key_name = matched


def client_address(request: Request) -> str:
    """The caller's address: from CLIENT_IP_HEADER when set (Railway: x-real-ip,
    which its edge proxy overwrites), otherwise the TCP peer."""
    header = config.CLIENT_IP_HEADER
    if header:
        value = request.headers.get(header, "")
        if header == "x-forwarded-for":
            value = value.split(",")[-1]   # the entry the proxy itself added
        if value.strip():
            return value.strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, x_device_id: Optional[str] = Header(None, max_length=64)) -> None:
    request.app.state.limiter.check_request(x_device_id, client_address(request))


class _NoScanSlot(Exception):
    pass


@contextmanager
def _scan_slot(request: Request):
    """Caps scans running at once (MAX_CONCURRENT_SCANS); past it, a quick 503."""
    slots = request.app.state.scan_slots
    if not slots.acquire(blocking=False):
        raise _NoScanSlot()
    try:
        yield
    finally:
        slots.release()


_LIMIT_SPEECH = {"too_fast": speech.TOO_FAST, "daily_limit": speech.DAILY_LIMIT,
                 "service_limit": speech.SERVICE_LIMIT}


def _limited(reason: str, retry_after_s: int) -> JSONResponse:
    return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after_s)}, content={
        "status": "rate_limited",
        "reason": reason,
        "retry_after_s": retry_after_s,
        "spoken_text": _LIMIT_SPEECH[reason],
        "request_id": request_id_var.get(),
    })


@app.exception_handler(RateLimited)
def _rate_limited(request: Request, exc: RateLimited) -> JSONResponse:
    return _limited(exc.reason, exc.retry_after_s)


@app.exception_handler(_NoScanSlot)
def _no_scan_slot(request: Request, exc: _NoScanSlot) -> JSONResponse:
    log.warning("too many scans at once", extra={"fields": {"max": config.MAX_CONCURRENT_SCANS}})
    return JSONResponse(status_code=503, headers={"Retry-After": "2"}, content={
        "status": "service_unavailable", "spoken_text": speech.SERVICE_BUSY, "request_id": request_id_var.get()})


GUARDS = [Depends(require_api_key), Depends(rate_limit)]   # in this order: no counting for a wrong key


def _service_busy(e: LLMUnavailableError) -> JSONResponse:
    log.error("llm unavailable", extra={"fields": {"error": str(e)}})
    return JSONResponse(status_code=503, content={
        "status": "service_unavailable",
        "spoken_text": speech.SERVICE_BUSY,
        "request_id": request_id_var.get(),
    })


@app.get("/health")
def health(request: Request) -> dict:
    kb = request.app.state.kb
    return {"status": "ok", "llm_provider": config.LLM_PROVIDER, "kb_version": kb.version,
            "kb_products": len(kb.products)}


# --- v2 ---

class BlockIn(BaseModel):
    text: str = Field(..., max_length=500)
    confidence: Optional[float] = Field(None, ge=0, le=1)
    box: Optional[list[float]] = Field(
        None, min_length=4, max_length=4,
        description="left, top, right, bottom as fractions (0-1) of the image")


class CaptureHintsIn(BaseModel):
    blurry: bool = False
    dark: bool = False
    text_small: bool = False


class IdentifyV2Request(BaseModel):
    text: str = Field("", max_length=config.MAX_OCR_TEXT_CHARS,
                      description="All recognized text, blocks joined by newlines.")
    blocks: list[BlockIn] = Field(default_factory=list, max_length=config.MAX_BLOCKS)
    ocr_confidence: Optional[float] = Field(
        None, ge=0, le=1, description="Mean OCR confidence; null if the platform doesn't report one.")
    attempt: int = Field(1, ge=1, le=100, description="Rescans of the same item, starting at 1.")
    capture_hints: CaptureHintsIn = Field(default_factory=CaptureHintsIn)
    platform: str = Field("unknown", max_length=20)
    app_version: Optional[str] = Field(None, max_length=40)


class ProductOut(BaseModel):
    brand: Optional[str]
    generics: list[str]
    strengths: list[str]
    prescription: bool


class IdentifyV2Response(BaseModel):
    status: Literal["ok", "ok_caveat", "rescan", "multiple", "gave_up"]
    rescan_reason: Optional[Literal["no_text", "too_much_text", "no_name", "name_not_in_text",
                                    "strength_unread", "ingredients_incomplete"]]
    hint: Optional[Literal["move_closer", "more_light", "turn_box"]]
    tier: Optional[Literal["reviewed", "ingredient", "web", "general"]]
    product: Optional[ProductOut]
    description: Optional[str]
    sources: list[str]
    spoken_text: str
    request_id: Optional[str]
    cache: Optional[Literal["hit", "miss"]]


def _box(box: Optional[list[float]]) -> Optional[tuple[float, float, float, float]]:
    # Boxes outside 0-1 or upside down are ignored rather than refused: they only refine a heuristic.
    if box and all(0 <= v <= 1 for v in box) and box[0] <= box[2] and box[1] <= box[3]:
        return tuple(box)
    return None


# Sync endpoints on purpose: the pipeline makes blocking HTTP calls, and
# FastAPI runs sync endpoints in a thread pool.
@app.post("/v2/identify", response_model=IdentifyV2Response, dependencies=GUARDS)
def identify_v2_endpoint(req: IdentifyV2Request, request: Request,
                         x_device_id: Optional[str] = Header(None, max_length=64)):
    scan = ScanRequest(
        text=req.text or "\n".join(b.text for b in req.blocks),
        blocks=[Block(b.text, b.confidence, _box(b.box)) for b in req.blocks],
        ocr_confidence=req.ocr_confidence,
        attempt=req.attempt,
        hints=CaptureHints(**req.capture_hints.model_dump()),
        meta={"client": "mobile", "api": "v2", "platform": req.platform, "app_version": req.app_version,
              "device": hash_device_id(x_device_id), "block_count": len(req.blocks),
              "key": request.state.key_name},
    )
    budget = request.app.state.limiter.budget_for(request.state.key_name)
    web_budget = request.app.state.limiter.web_budget_for(scan.meta["device"])
    try:
        with _scan_slot(request):
            result = identify_scan(scan, request.app.state.cache, request.app.state.kb, budget, web_budget)
    except LLMBudgetExceeded as e:
        return _limited("service_limit", e.retry_after_s)
    except LLMUnavailableError as e:
        return _service_busy(e)
    return {**result, "request_id": request_id_var.get()}


# --- v1 ---

class OCRLine(BaseModel):
    text: str
    confidence: Optional[float] = Field(None, ge=0, le=1)


class IdentifyRequest(BaseModel):
    text: str = Field("", max_length=config.MAX_OCR_TEXT_CHARS,
                      description="All recognized text, lines joined by newlines.")
    lines: list[OCRLine] = Field(default_factory=list, max_length=config.MAX_BLOCKS)
    ocr_confidence: Optional[float] = Field(
        None, ge=0, le=1, description="Mean OCR confidence; null if the platform doesn't report one.")
    frames_merged: Optional[int] = Field(None, ge=1)
    platform: str = Field("unknown", max_length=20)
    app_version: Optional[str] = Field(None, max_length=40)


class IdentifyResponse(BaseModel):
    status: Literal["ok", "low_confidence", "rescan"]
    brand_name: Optional[str]
    generic_name: Optional[str]
    dosage: Optional[str]
    description: Optional[str]
    grounded: bool
    source_url: Optional[str]
    warnings: list[str]
    spoken_text: str
    cache: Optional[Literal["hit", "miss"]]


@app.post("/identify", response_model=IdentifyResponse, dependencies=GUARDS)
def identify_endpoint(req: IdentifyRequest, request: Request):
    raw_text = req.text or "\n".join(line.text for line in req.lines)
    meta = {
        "client": "mobile",
        "api": "v1",
        "platform": req.platform,
        "app_version": req.app_version,
        "key": request.state.key_name,
        "frames_merged": req.frames_merged,
        "ocr_lines": [line.model_dump() for line in req.lines],
    }
    budget = request.app.state.limiter.budget_for(request.state.key_name)
    # v1 sends no device ID, so its web lookups share one daily bucket.
    web_budget = request.app.state.limiter.web_budget_for(None)
    try:
        with _scan_slot(request):
            return identify(raw_text, req.ocr_confidence, request.app.state.cache, meta=meta,
                            kb=request.app.state.kb, budget=budget, web_budget=web_budget)
    except LLMBudgetExceeded as e:
        return _limited("service_limit", e.retry_after_s)
    except LLMUnavailableError as e:
        return _service_busy(e)
