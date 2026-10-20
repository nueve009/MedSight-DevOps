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

Every response carries an X-Request-Id header (the client's own, if it sent a
valid one), and every log line for that request includes the same ID.
"""
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config, speech
from app.cache_store import CacheStore
from app.llm_client import LLMUnavailableError
from app.logging_setup import RequestIdMiddleware, configure_logging, request_id_var
from app.pipeline import ScanRequest, identify, identify_scan
from app.scan_rules import Block, CaptureHints
from app.utils import hash_device_id

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(config.LOG_LEVEL)
    config.ensure_dirs()
    app.state.cache = CacheStore()
    log.info("startup", extra={"fields": {"llm_provider": config.LLM_PROVIDER}})
    yield


class BodySizeLimitMiddleware:
    """Refuses request bodies over config.MAX_BODY_BYTES with 413, whether or
    not the client sent a Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = config.MAX_BODY_BYTES
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


app = FastAPI(title="MedSight backend", lifespan=lifespan)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestIdMiddleware)   # added last = outermost, so even a 413 gets an ID


def require_api_key(x_api_key: str = Header("")) -> None:
    if not config.MEDSIGHT_API_KEY:
        # Fail closed: an unset key must not leave the LLM quota open to anyone.
        raise HTTPException(status_code=503, detail="Server API key is not configured.")
    if not secrets.compare_digest(x_api_key.encode(), config.MEDSIGHT_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _service_busy(e: LLMUnavailableError) -> JSONResponse:
    log.error("llm unavailable", extra={"fields": {"error": str(e)}})
    return JSONResponse(status_code=503, content={
        "status": "service_unavailable",
        "spoken_text": speech.SERVICE_BUSY,
        "request_id": request_id_var.get(),
    })


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_provider": config.LLM_PROVIDER}


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
@app.post("/v2/identify", response_model=IdentifyV2Response, dependencies=[Depends(require_api_key)])
def identify_v2_endpoint(req: IdentifyV2Request, request: Request,
                         x_device_id: Optional[str] = Header(None, max_length=64)):
    scan = ScanRequest(
        text=req.text or "\n".join(b.text for b in req.blocks),
        blocks=[Block(b.text, b.confidence, _box(b.box)) for b in req.blocks],
        ocr_confidence=req.ocr_confidence,
        attempt=req.attempt,
        hints=CaptureHints(**req.capture_hints.model_dump()),
        meta={"client": "mobile", "api": "v2", "platform": req.platform, "app_version": req.app_version,
              "device": hash_device_id(x_device_id), "block_count": len(req.blocks)},
    )
    try:
        result = identify_scan(scan, request.app.state.cache)
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


@app.post("/identify", response_model=IdentifyResponse, dependencies=[Depends(require_api_key)])
def identify_endpoint(req: IdentifyRequest, request: Request):
    raw_text = req.text or "\n".join(line.text for line in req.lines)
    meta = {
        "client": "mobile",
        "api": "v1",
        "platform": req.platform,
        "app_version": req.app_version,
        "frames_merged": req.frames_merged,
        "ocr_lines": [line.model_dump() for line in req.lines],
    }
    try:
        return identify(raw_text, req.ocr_confidence, request.app.state.cache, meta=meta)
    except LLMUnavailableError as e:
        return _service_busy(e)
