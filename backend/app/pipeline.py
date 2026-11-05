"""
Everything after OCR, in order. Orchestration only: each step lives in its
own module.

    input checks (scan_rules)      no letters / too much text -> rescan, no model call
    raw-text parse cache           the same label again costs no LLM call
    parse (llm_parse)              every product, ingredients and strengths
    scan rules (scan_rules)        rescan / multiple / gave_up / ok / ok_caveat
    dataset match (retrieval)      reviewed -> answer from kb/; partial -> rescan;
                                   ingredient -> answer from reviewed ingredient text
    answer cache                   (general and web tiers) keyed on brand + every ingredient and strength
    web lookup (web_search)        unlisted branded product, no cached web answer or "not found"
    description (description_agent, "general" tier: no web answer and a cache miss)
    spoken text (speech)

Reviewed and ingredient answers come from the dataset in memory, never from
the cache or an LLM, so a dataset change takes effect on the next scan.

identify_scan() returns the API v2 response (without request_id).
identify() is the v1 adapter: the same pipeline, mapped to the v1 shape so
older app builds keep working.

Cache and scan-log problems never fail a scan (rule 4 of the plan): a failed
read is a miss and a failed write is skipped, both logged.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from app import config, retrieval, scan_rules, speech, web_search
from app.description_agent import get_description
from app.llm_parse import PARSE_PROMPT_VERSION, ParsedProduct, ParseResult, parse_label
from app.ratelimit import WebLimitReached
from app.retrieval import KnowledgeBase, Match
from app.scan_rules import Block, CaptureHints, Decision
from app.store import Store, StoreSkipped
from app.utils import answer_key, raw_text_key

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
    except StoreSkipped:
        return None   # the database is down; the circuit breaker already logged it
    except Exception as e:  # any cache error must fail open
        log.warning("cache error", extra={"fields": {"action": action, "error_class": type(e).__name__}})
        return None


def _parse(text: str, cache: Store, record: dict, budget: Callable[[], None]) -> ParseResult:
    key = raw_text_key(text, _model_name(), PARSE_PROMPT_VERSION)
    cached = _cache_call("get_parse", cache.get_parse, key)
    if cached is not None:
        record["parse_cache"] = "hit"
        return ParseResult.from_dict(cached)

    record["parse_cache"] = "miss"
    budget()   # raises LLMBudgetExceeded when the day's model calls are used up
    started = time.perf_counter()
    result = parse_label(text)
    record["parse_ms"] = round((time.perf_counter() - started) * 1000)
    # Only a usable parse is cached; an empty or invalid one is tried again.
    if result.valid and result.products:
        _cache_call("set_parse", cache.set_parse, key, result.to_dict())
    return result


def _product_json(product: ParsedProduct, rx: bool = False) -> dict:
    return {"brand": product.brand, "generics": list(product.generics),
            "strengths": list(product.strengths), "prescription": rx}


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
        # Internal: for the scan log and eval_kb.py. The API's response model drops it.
        "matched_product_id": answer.get("matched_product_id"),
    }


def _no_answer(decision: Decision) -> dict:
    if decision.status == "multiple":
        spoken = speech.MULTIPLE
    elif decision.status == "gave_up":
        spoken = speech.gave_up(decision.reason, decision.product)
    else:
        spoken = speech.rescan(decision.reason, decision.hint, decision.product)
    return _response(decision, spoken)


def _web(product, key: str, cache: Store, record: dict, budget: Callable[[], None],
         web_budget: Callable[[], None]) -> Optional[web_search.WebAnswer]:
    """A new web answer (cached as pending_review), or None: the web tier is
    off, the product has no brand, a recent search found nothing, a cap is used
    up, or the search failed. Only a model failure raises."""
    if not web_search.enabled() or not product.brand:
        record["web"] = "off"
        return None
    if web_search.miss_is_live(_cache_call("get_web_miss", cache.get_parse, web_search.miss_key(key)), time.time()):
        record["web"] = "cached_not_found"
        return None
    try:
        web_budget()
        found = web_search.lookup(product, record, budget)
    except WebLimitReached:
        record["web"] = "limit"
        return None
    except web_search.WebSearchUnavailable as e:
        log.warning("web search unavailable", extra={"fields": {"error": str(e)}})
        record["web"] = "unavailable"   # temporary: nothing cached, the next scan tries again
        return None
    if found is None:
        _cache_call("set_web_miss", cache.set_parse, web_search.miss_key(key), web_search.miss_marker(time.time()))
    else:
        _cache_call("set_answer", cache.set_answer, key, speech.WEB, product.to_dict(), found.description,
                    [found.source_url], "pending_review")
    return found


def _answer(decision: Decision, match: Match, cache: Store, record: dict, budget: Callable[[], None],
            web_budget: Callable[[], None]) -> dict:
    caveat = decision.status == "ok_caveat"
    if match.kind == retrieval.REVIEWED:
        # Speak the dataset's own spelling, not the OCR's ("Bi0gesic" -> "Biogesic").
        k = match.product
        spoken = speech.answer(k.as_parsed(), speech.REVIEWED, match.description, rx=k.rx, caveat=caveat)
        return _response(decision, spoken, tier=speech.REVIEWED, product=_product_json(k.as_parsed(), k.rx),
                         description=match.description, sources=list(match.sources),
                         matched_product_id=k.product_id)

    product = decision.product
    if match.kind == retrieval.INGREDIENT:
        spoken = speech.answer(product, speech.INGREDIENT, match.description, caveat=caveat)
        return _response(decision, spoken, tier=speech.INGREDIENT, product=_product_json(product),
                         description=match.description, sources=list(match.sources))

    key = answer_key(product.brand, product.generics, product.strengths)
    cached = _cache_call("get_answer", cache.get_answer, key)
    found = None if cached and cached["tier"] == speech.WEB else _web(product, key, cache, record, budget,
                                                                      web_budget)
    if found:
        tier, description, sources, cache_status = speech.WEB, found.description, [found.source_url], "miss"
    elif cached:
        tier, description, sources, cache_status = cached["tier"], cached["description"], cached["sources"], "hit"
    else:
        budget()
        started = time.perf_counter()
        result = get_description(product)
        record["describe_ms"] = round((time.perf_counter() - started) * 1000)
        record["description_status"] = result["status"]
        tier, description, sources, cache_status = speech.GENERAL, result["description"], [], "miss"
        if result["status"] == "ok":
            _cache_call("set_answer", cache.set_answer, key, tier, product.to_dict(), description, sources)

    site = web_search.site_name(sources[0]) if tier == speech.WEB and sources else None
    spoken = speech.answer(product, tier, description, caveat=caveat, source_site=site)
    return _response(decision, spoken, tier=tier, product=_product_json(product),
                     description=description, sources=sources, cache=cache_status)


def _finish(record: dict, response: dict, started: float, cache: Store) -> dict:
    record.update({k: response[k] for k in ("status", "rescan_reason", "hint", "tier", "cache",
                                            "matched_product_id")})
    record["final_spoken_text"] = response["spoken_text"]
    # The scan log: the local run log (SQLite) or scan_log (Postgres). Fails open like the cache.
    _cache_call("log_scan", cache.log_scan, record)
    # One stdout line per scan. No OCR text: that stays in the scan log.
    log.info("scan", extra={"fields": {
        **{k: record.get(k) for k in ("status", "rescan_reason", "hint", "tier", "cache", "parse_cache",
                                       "match", "matched_product_id", "kb_version", "attempt",
                                       "product_count", "parse_confidence", "description_status",
                                       "parse_ms", "describe_ms", "web", "web_ms", "client", "platform",
                                       "app_version", "device",
                                       "key")},
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }})
    return response


def identify_scan(req: ScanRequest, cache: Store, kb: Optional[KnowledgeBase] = None,
                  budget: Optional[Callable[[], None]] = None,
                  web_budget: Optional[Callable[[], None]] = None) -> dict:
    """Run the post-OCR pipeline against the reviewed dataset kb (None = empty dataset).
    budget runs before each model call (ratelimit.Limiter.budget_for); None = no budget.
    web_budget runs before each web lookup (Limiter.web_budget_for) and raises
    WebLimitReached past a cap; None = no cap.
    Raises llm_client.LLMUnavailableError if the LLM can't be reached, or its
    subclass ratelimit.LLMBudgetExceeded when the budget is used up."""
    budget = budget or (lambda: None)
    web_budget = web_budget or (lambda: None)
    kb = KnowledgeBase() if kb is None else kb
    started = time.perf_counter()
    record = {**req.meta, "raw_ocr_text": req.text, "ocr_confidence": req.ocr_confidence, "attempt": req.attempt}
    if req.ocr_confidence is None:
        record["ocr_confidence_skipped"] = True

    reason = scan_rules.input_reason(req.text)
    if reason:
        return _finish(record, _no_answer(scan_rules.rescan(reason, req.hints, req.attempt)), started, cache)

    parse = _parse(req.text, cache, record, budget)
    record["parsed"] = parse.to_dict()
    record["parse_confidence"] = parse.confidence
    record["product_count"] = len(parse.products)
    if not parse.valid:
        decision = scan_rules.rescan(scan_rules.NO_NAME, req.hints, req.attempt)
    else:
        decision = scan_rules.decide(parse.products, req.text, req.blocks, req.attempt, req.hints,
                                     req.ocr_confidence)

    if decision.status not in ANSWER_STATUSES:
        return _finish(record, _no_answer(decision), started, cache)

    match = kb.match(decision.product)
    record["match"], record["kb_version"] = match.kind, kb.version
    if match.kind == retrieval.PARTIAL:
        # A listed brand with a missing or different ingredient or strength:
        # never answered, and named with the dataset's spelling.
        decision = scan_rules.rescan(match.reason, req.hints, req.attempt, match.product.as_parsed())
        return _finish(record, _no_answer(decision), started, cache)
    return _finish(record, _answer(decision, match, cache, record, budget, web_budget), started, cache)


# --- v1 adapter ---

V1_NOT_VERIFIED = "not verified"   # v1 warning value; kept as is for older app builds


def identify(raw_text: str, ocr_confidence: Optional[float], cache: Store,
             meta: Optional[dict] = None, kb: Optional[KnowledgeBase] = None,
             budget: Optional[Callable[[], None]] = None,
             web_budget: Optional[Callable[[], None]] = None) -> dict:
    """POST /identify (v1). Same pipeline as v2, mapped to the v1 response shape:
    ok -> ok, ok_caveat -> low_confidence, everything else -> rescan."""
    r = identify_scan(ScanRequest(text=raw_text, ocr_confidence=ocr_confidence, meta=meta or {}), cache, kb, budget,
                      web_budget)
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
