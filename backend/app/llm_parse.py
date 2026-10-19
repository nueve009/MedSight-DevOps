"""
Step 3 of the pipeline: send raw OCR text to the LLM (see llm_client.py) and extract
{generic_name, brand_name, dosage} as structured JSON, with a confidence score.

Small local models tend to invent plausible-sounding names, so after parsing
we check that the extracted names actually appear (even approximately) in the
OCR text, and cap the confidence if they don't.
"""
import difflib
import json
import re

from app import llm_client

PARSE_SYSTEM_PROMPT = """You extract structured medicine info from noisy OCR text \
scraped off Philippine OTC/branded medicine packaging.

Return JSON with these fields:
- generic_name: the active ingredient (e.g. "Paracetamol"), or "" if not present
- brand_name: the product's brand (e.g. "Biogesic"), or "" if not present
- dosage: strength with unit (e.g. "500mg"), or "" if not present
- confidence: number from 0 to 1 for how sure you are, given how garbled the text is

Rules:
- Only use words that appear (even misspelled) in the OCR text. Never invent a name.
- If a field is not in the text, use an empty string."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "generic_name": {"type": "string"},
        "brand_name": {"type": "string"},
        "dosage": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["generic_name", "brand_name", "dosage", "confidence"],
}

_EMPTY = {"generic_name": None, "brand_name": None, "dosage": None, "confidence": 0.0}


def _appears_in_text(value: str, raw_text: str) -> bool:
    """True if the value (or any word in it, fuzzily) shows up in the OCR text."""
    if not value:
        return False
    raw_lower = raw_text.lower()
    value_lower = value.lower().strip()
    if value_lower in raw_lower:
        return True
    ocr_words = re.findall(r"[a-z0-9]+", raw_lower)
    for token in re.findall(r"[a-z0-9]+", value_lower):
        if len(token) >= 4 and difflib.get_close_matches(token, ocr_words, n=1, cutoff=0.8):
            return True
    return False


def parse_medicine_text(raw_ocr_text: str) -> dict:
    """Returns {generic_name, brand_name, dosage, confidence}."""
    if not raw_ocr_text.strip():
        return dict(_EMPTY)

    reply = llm_client.chat(PARSE_SYSTEM_PROMPT, f"OCR text:\n{raw_ocr_text}", schema=_SCHEMA)

    try:
        parsed = json.loads(reply)
    except json.JSONDecodeError:
        return dict(_EMPTY)

    result = {
        "generic_name": (parsed.get("generic_name") or "").strip() or None,
        "brand_name": (parsed.get("brand_name") or "").strip() or None,
        "dosage": (parsed.get("dosage") or "").strip() or None,
        "confidence": float(parsed.get("confidence") or 0.0),
    }

    # Anti-hallucination guard: at least one extracted name must be in the OCR text.
    names = [n for n in (result["generic_name"], result["brand_name"]) if n]
    if names and not any(_appears_in_text(n, raw_ocr_text) for n in names):
        result["confidence"] = min(result["confidence"], 0.3)

    return result