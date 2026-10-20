"""Shared helpers: strength and cache-key normalization, and JSONL logging."""
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


def normalize_key(generic_name: str, brand_name: str, dosage: str) -> str:
    """
    Cache key: a 16-character sha256 of the normalized generic name, brand
    name and dosage. Repeat photos of the same product won't OCR identically,
    so we key on the *parsed* fields, not raw OCR text.
    """
    combined = f"{clean_text(generic_name)}|{clean_text(brand_name)}|{normalize_strength(dosage)}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def append_jsonl(path: Path, record: dict) -> None:
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
