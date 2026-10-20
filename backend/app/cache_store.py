"""
SQLite cache with two tables:

- parses:  raw-text cache in front of the parse step. Key = utils.raw_text_key
           (normalized OCR text + model + prompt version). Holds only valid
           parse results, so the same label sent again costs no LLM call.
           Scan rules and matching still run on every scan.
- answers: descriptions, keyed by utils.answer_key (brand + every ingredient
           and strength), with their tier and sources. Only "ok" descriptions
           are stored.

Safe for the backend's concurrent requests: each call opens its own
connection, and WAL mode lets readers proceed while a write is in progress.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parses (
    cache_key  TEXT PRIMARY KEY,
    result     TEXT NOT NULL,          -- ParseResult.to_dict() as JSON
    cached_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    cache_key   TEXT PRIMARY KEY,
    tier        TEXT NOT NULL,
    product     TEXT NOT NULL,         -- ParsedProduct.to_dict() as JSON
    description TEXT NOT NULL,
    sources     TEXT NOT NULL,         -- JSON list of URLs
    cached_at   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CacheStore:
    def __init__(self, db_path: Optional[Path] = None):
        # Read the setting at call time, not import time, so tests can point it elsewhere.
        self.db_path = db_path or config.CACHE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

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

    def get_parse(self, cache_key: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT result FROM parses WHERE cache_key = ?", (cache_key,)).fetchone()
        return json.loads(row["result"]) if row else None

    def set_parse(self, cache_key: str, result: dict) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO parses VALUES (?, ?, ?)",
                         (cache_key, json.dumps(result), _now()))

    def get_answer(self, cache_key: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM answers WHERE cache_key = ?", (cache_key,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["product"] = json.loads(record["product"])
        record["sources"] = json.loads(record["sources"])
        return record

    def set_answer(self, cache_key: str, tier: str, product: dict, description: str, sources: list[str]) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?, ?)",
                         (cache_key, tier, json.dumps(product), description, json.dumps(sources), _now()))
