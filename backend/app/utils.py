"""Shared helpers: cache-key normalization and JSONL logging."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def normalize_key(generic_name: str, brand_name: str, dosage: str) -> str:
    """
    Build the cache key exactly as specified in the handoff doc:
    normalized hash of generic_name + brand_name + dosage (lowercase, trimmed).
    Repeat photos of the same product won't OCR identically, so we key on the
    *parsed* fields, not raw OCR text.
    """
    def clean(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        return s

    combined = f"{clean(generic_name)}|{clean(brand_name)}|{clean(dosage)}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def append_jsonl(path: Path, record: dict) -> None:
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def safe_slug(text: str, max_len: int = 60) -> str:
    """Filesystem-safe slug for note filenames, e.g. 'Biogesic 500mg'."""
    text = (text or "unknown").strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return (text or "unknown")[:max_len]
