"""
SQLite cache for medicine descriptions, keyed by utils.normalize_key().

Same get/set interface as obsidian_cache.ObsidianCache, but safe for the
backend's concurrent requests: each call opens its own connection, and WAL
mode lets readers proceed while a write is in progress.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS medicines (
    cache_key    TEXT PRIMARY KEY,
    generic_name TEXT NOT NULL,
    brand_name   TEXT NOT NULL,
    dosage       TEXT NOT NULL,
    description  TEXT NOT NULL,
    grounded     INTEGER NOT NULL,
    source_url   TEXT,
    confidence   REAL NOT NULL,
    cached_at    TEXT NOT NULL
)
"""


class CacheStore:
    def __init__(self, db_path: Path = config.CACHE_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # sqlite3's own context manager commits but never closes, so wrap it.
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, cache_key: str) -> Optional[dict]:
        """Returns the cached record dict, or None on a miss."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM medicines WHERE cache_key = ?", (cache_key,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["grounded"] = bool(record["grounded"])
        return record

    def set(self, cache_key: str, generic_name: str, brand_name: str, dosage: str,
            description: str, grounded: bool, source_url: Optional[str], confidence: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO medicines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cache_key, generic_name, brand_name, dosage, description, int(grounded),
                 source_url, confidence, datetime.now(timezone.utc).isoformat()),
            )
