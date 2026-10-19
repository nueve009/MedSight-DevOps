"""
MedSight backend: receives OCR text from the mobile app and returns what to
read aloud. OCR and text-to-speech run on the phone; this runs the rest of the
pipeline (see pipeline.py).

Run locally:
    uvicorn app.api:app --reload   (from the backend/ folder)

Endpoints:
    GET  /health    liveness check for the cloud host
    POST /identify  OCR text in, identification + spoken_text out (needs X-API-Key)
"""
import secrets
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config
from app.cache_store import CacheStore
from app.llm_client import LLMUnavailableError
from app.pipeline import identify

SERVICE_BUSY_MESSAGE = ("The identification service is busy or unavailable right now. "
                        "Please try again in a moment.")

app = FastAPI(title="MedSight backend")
cache = CacheStore()


class OCRLine(BaseModel):
    text: str
    confidence: Optional[float] = Field(None, ge=0, le=1)


class IdentifyRequest(BaseModel):
    text: str = Field("", max_length=config.MAX_OCR_TEXT_CHARS,
                      description="All recognized text, lines joined by newlines.")
    lines: list[OCRLine] = Field(default_factory=list, max_length=500)
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


def require_api_key(x_api_key: str = Header("")) -> None:
    if not config.MEDSIGHT_API_KEY:
        # Fail closed: an unset key must not leave the LLM quota open to anyone.
        raise HTTPException(status_code=503, detail="Server API key is not configured.")
    if not secrets.compare_digest(x_api_key, config.MEDSIGHT_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_provider": config.LLM_PROVIDER}


# Sync endpoint on purpose: the pipeline makes blocking HTTP calls, and
# FastAPI runs sync endpoints in a thread pool.
@app.post("/identify", response_model=IdentifyResponse, dependencies=[Depends(require_api_key)])
def identify_endpoint(req: IdentifyRequest):
    raw_text = req.text or "\n".join(line.text for line in req.lines)
    meta = {
        "client": "mobile",
        "platform": req.platform,
        "app_version": req.app_version,
        "frames_merged": req.frames_merged,
        "ocr_lines": [line.model_dump() for line in req.lines],
    }
    try:
        return identify(raw_text, req.ocr_confidence, cache, meta=meta)
    except LLMUnavailableError as e:
        print(f"LLM unavailable: {e}")
        return JSONResponse(status_code=503, content={
            "status": "service_unavailable",
            "spoken_text": SERVICE_BUSY_MESSAGE,
        })
