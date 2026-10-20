"""
Everything after OCR, in order. Orchestration only: each step lives in its
own module.

    input checks (scan_rules)      no letters / too much text -> rescan, no model call
    raw-text parse cache           the same label again costs no LLM call
    parse (llm_parse)              every product, ingredients and strengths
    scan rules (scan_rules)        rescan / multiple / gave_up / ok / ok_caveat
    answer cache                   keyed on brand + every ingredient and strength
    description (description_agent, "general" tier, on a cache miss)
    spoken text (speech)

identify_scan() returns the API v2 response (without request_id).
identify() is the v1 adapter: the same pipeline, mapped to the v1 shape so
older app builds keep working.

Cache problems never fail a scan (rule 4 of the plan): a failed read is a
miss and a failed write is skipped, both logged.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from app import config, scan_rules, speech
from app.cache_store import CacheStore
from app.description_agent import get_description
from app.llm_parse import PARSE_PROMPT_VERSION, ParsedProduct, ParseResult, parse_label
from app.scan_rules import Block, CaptureHints, Decision
from app.utils import answer_key, append_jsonl, raw_text_key

log = logging.getLogger(__name__)

ANSWER_STATUSES = ("ok", "ok_caveat")


@dataclass
class ScanRequest:
    text: str
    blocks: list[Block] = field(default_factory=list)
    ocr_confidence: Optional[float] = None
    attempt: int = 1
    hints: CaptureHints = field(default_factory=CaptureHints)
    meta: dict = field(default_factory=dict)   # client, platform, app_version, device (hashed)


def _model_name() -> str:
    return config.GROQ_MODEL if config.LLM_PROVIDER == "groq" else f"{config.LLM_PROVIDER}:{config.OLLAMA_MODEL}"


def _cache_call(action: str, fn: Callable, *args):
    try:
        return fn(*args)
    except Exception as e:  # any cache error must fail open
        log.warning("cache error", extra={"fields": {"action": action, "error_class": type(e).__name__}})
        return None


def _parse(text: str, cache: CacheStore, record: dict) -> ParseResult:
    key = raw_text_key(text, _model_name(), PARSE_PROMPT_VERSION)
    cached = _cache_call("get_parse", cache.get_parse, key)
    if cached is not None:
        record["parse_cache"] = "hit"
        return ParseResult.from_dict(cached)

    record["parse_cache"] = "miss"
    started = time.perf_counter()
    result = parse_label(text)
    record["parse_ms"] = round((time.perf_counter() - started) * 1000)
    # Only a usable parse is cached; an empty or invalid one is tried again.
    if result.valid and result.products:
        _cache_call("set_parse", cache.set_parse, key, result.to_dict())
    return result


def _product_json(product: ParsedProduct, prescription: bool = False) -> dict:
    return {"brand": product.brand, "generics": list(product.generics),
            "strengths": list(product.strengths), "prescription": prescription}


def _response(decision: Decision, spoken_text: str, **answer) -> dict:
    return {
        "status": decision.status,
        "rescan_reason": decision.reason,
        "hint": decision.hint,
        "tier": answer.get("tier"),
        "product": answer.get("product"),
        "description": answer.get("description"),
        "sources": answer.get("sources", []),
        "spoken_text": spoken_text,
        "cache": answer.get("cache"),
    }


def _no_answer(decision: Decision) -> dict:
    if decision.status == "multiple":
        spoken = speech.MULTIPLE
    elif decision.status == "gave_up":
        spoken = speech.gave_up(decision.reason, decision.product)
    else:
        spoken = speech.rescan(decision.reason, decision.hint, decision.product)
    return _response(decision, spoken)


def _answer(decision: Decision, cache: CacheStore, record: dict) -> dict:
    product = decision.product
    # (M2: match against the reviewed dataset in kb/ goes here, before the general tier.)
    key = answer_key(product.brand, product.generics, product.strengths)
    cached = _cache_call("get_answer", cache.get_answer, key)
    if cached:
        tier, description, sources, cache_status = cached["tier"], cached["description"], cached["sources"], "hit"
    else:
        started = time.perf_counter()
        result = get_description(product)
        record["describe_ms"] = round((time.perf_counter() - started) * 1000)
        record["description_status"] = result["status"]
        tier, description, sources, cache_status = speech.GENERAL, result["description"], [], "miss"
        if result["status"] == "ok":
            _cache_call("set_answer", cache.set_answer, key, tier, product.to_dict(), description, sources)

    spoken = speech.answer(product, tier, description, caveat=decision.status == "ok_caveat")
    return _response(decision, spoken, tier=tier, product=_product_json(product),
                     description=description, sources=sources, cache=cache_status)


def _finish(record: dict, response: dict, started: float) -> dict:
    record.update({k: response[k] for k in ("status", "rescan_reason", "hint", "tier", "cache")})
    record["final_spoken_text"] = response["spoken_text"]
    append_jsonl(config.RUN_LOG_PATH, record)
    # One stdout line per scan. No OCR text: that stays in the local run log.
    log.info("scan", extra={"fields": {
        **{k: record.get(k) for k in ("status", "rescan_reason", "hint", "tier", "cache", "parse_cache",
                                       "attempt", "product_count", "parse_confidence", "description_status",
                                       "parse_ms", "describe_ms", "client", "platform", "app_version", "device")},
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }})
    return response


def identify_scan(req: ScanRequest, cache: CacheStore) -> dict:
    """Run the post-OCR pipeline. Raises llm_client.LLMUnavailableError if the LLM can't be reached."""
    started = time.perf_counter()
    record = {**req.meta, "raw_ocr_text": req.text, "ocr_confidence": req.ocr_confidence, "attempt": req.attempt}
    if req.ocr_confidence is None:
        record["ocr_confidence_skipped"] = True

    reason = scan_rules.input_reason(req.text)
    if reason:
        return _finish(record, _no_answer(scan_rules.rescan(reason, req.hints, req.attempt)), started)

    parse = _parse(req.text, cache, record)
    record["parsed"] = parse.to_dict()
    record["parse_confidence"] = parse.confidence
    record["product_count"] = len(parse.products)
    if not parse.valid:
        decision = scan_rules.rescan(scan_rules.NO_NAME, req.hints, req.attempt)
    else:
        decision = scan_rules.decide(parse.products, req.text, req.blocks, req.attempt, req.hints,
                                     req.ocr_confidence)

    if decision.status in ANSWER_STATUSES:
        return _finish(record, _answer(decision, cache, record), started)
    return _finish(record, _no_answer(decision), started)


# --- v1 adapter ---

V1_NOT_VERIFIED = "not verified"   # v1 warning value; kept as is for older app builds


def identify(raw_text: str, ocr_confidence: Optional[float], cache: CacheStore,
             meta: Optional[dict] = None) -> dict:
    """POST /identify (v1). Same pipeline as v2, mapped to the v1 response shape:
    ok -> ok, ok_caveat -> low_confidence, everything else -> rescan."""
    r = identify_scan(ScanRequest(text=raw_text, ocr_confidence=ocr_confidence, meta=meta or {}), cache)
    if r["status"] not in ANSWER_STATUSES:
        return {"status": "rescan", "brand_name": None, "generic_name": None, "dosage": None,
                "description": None, "grounded": False, "source_url": None, "warnings": [],
                "spoken_text": r["spoken_text"], "cache": None}

    product = r["product"]
    reviewed = r["tier"] == speech.REVIEWED
    warnings = (["OCR"] if r["status"] == "ok_caveat" else []) + ([] if reviewed else [V1_NOT_VERIFIED])
    return {
        "status": "ok" if r["status"] == "ok" else "low_confidence",
        "brand_name": product["brand"],
        "generic_name": " + ".join(product["generics"]) or None,
        "dosage": " + ".join(product["strengths"]) or None,
        "description": r["description"],
        "grounded": reviewed,
        "source_url": r["sources"][0] if r["sources"] else None,
        "warnings": warnings,
        "spoken_text": r["spoken_text"],
        "cache": r["cache"],
    }
