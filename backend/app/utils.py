"""Shared helpers: text and strength normalization, cache keys, prompt fencing,
the dosing check, and JSONL logging."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def clean_text(s: str) -> str:
    """Lowercase, trimmed, with runs of whitespace collapsed to one space."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def normalize_strength(s: str) -> str:
    """'500 mg', '500mg' and '500 MG' -> '500mg'; '5 mg / 5 mL' -> '5mg/5ml'.

    Only spacing and case change, so different strengths or units
    ('50 mg', '500 mcg') stay different.
    """
    s = clean_text(s).replace("µ", "mc")
    s = re.sub(r"(\d)\s+(?=[a-z%])", r"\1", s)   # number then unit
    s = re.sub(r"\s*/\s*", "/", s)               # spaces around "per"
    return s


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def answer_key(brand: str | None, generics, strengths) -> str:
    """Answer-cache key: the brand plus every (ingredient, strength) pair, sorted.

    Never one ingredient alone, so a combination (Alaxan FR: ibuprofen +
    paracetamol) can never share an answer with a single-ingredient product
    (Biogesic: paracetamol).
    """
    pairs = sorted(f"{clean_text(g)}={normalize_strength(s)}" for g, s in zip(generics, strengths, strict=True))
    return _hash(f"{clean_text(brand or '')}|{'+'.join(pairs)}")


def raw_text_key(text: str, model: str, prompt_version: str) -> str:
    """Parse-cache key: the normalized OCR text plus the model and prompt that read it."""
    return _hash(f"{model}|{prompt_version}|{clean_text(text)}")


def hash_device_id(device_id: str | None) -> str | None:
    """Device IDs are only ever logged or stored hashed."""
    return _hash(f"device|{device_id}") if device_id else None


def fence_untrusted(text: str, tag: str) -> str:
    """Wrap untrusted text (OCR, fetched pages) in <tag>...</tag> for a prompt.
    Any copy of the tag inside the text is removed, so it can't close the fence early."""
    cleaned = re.sub(rf"</?\s*{re.escape(tag)}\s*>", " ", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{cleaned}\n</{tag}>"


# A description must never tell someone how much to take or how often.
_DOSING_PATTERNS = [
    re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|ml|iu|units?|tablets?|tabs?|capsules?|caps?|"
               r"teaspoons?|tsp|tablespoons?|tbsp|drops?|puffs?|sachets?)\b", re.IGNORECASE),
    re.compile(r"\bevery\s+\d+\s*(?:to\s+\d+\s*)?(?:hours?|hrs?)\b", re.IGNORECASE),
    re.compile(r"\b(?:once|twice|thrice|\d+\s+times)\s+(?:a|per|each)\s+day\b", re.IGNORECASE),
    re.compile(r"\b(?:daily|maximum|max\.?|recommended)\s+dose\b", re.IGNORECASE),
]


def contains_dosing(text: str) -> bool:
    return any(p.search(text or "") for p in _DOSING_PATTERNS)


def append_jsonl(path: Path, record: dict) -> None:
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
