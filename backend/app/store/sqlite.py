"""
SQLite version of the store (see store/__init__.py). Used by tests, on
laptops and by eval_kb.py. The scan log is appended to config.RUN_LOG_PATH.

Safe for the backend's concurrent requests: each call opens its own
connection, and WAL mode lets readers proceed while a write is in progress.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from app import config
from app.store import SERVED_STATUSES, check_answer, expires_at, now
from app.utils import append_jsonl

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
# Columns added in M3; older local cache files get them on open.
_ADDED_COLUMNS = {"status": "TEXT NOT NULL DEFAULT 'active'", "expires_at": "TEXT"}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class SQLiteStore:
    def __init__(self, db_path: Optional[Path] = None):
        # Read the setting at call time, not import time, so tests can point it elsewhere.
        self.db_path = db_path or config.CACHE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            have = {r["name"] for r in conn.execute("PRAGMA table_info(answers)")}
            for name, ddl in _ADDED_COLUMNS.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE answers ADD COLUMN {name} {ddl}")

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

    # --- parses ---

    def get_parse(self, cache_key: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT result FROM parses WHERE cache_key = ?", (cache_key,)).fetchone()
        return json.loads(row["result"]) if row else None

    def set_parse(self, cache_key: str, result: dict) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO parses (cache_key, result, cached_at) VALUES (?, ?, ?)",
                         (cache_key, json.dumps(result), now().isoformat()))

    # --- answers ---

    @staticmethod
    def _answer(row: sqlite3.Row) -> dict:
        record = dict(row)
        record["product"] = json.loads(record["product"])
        record["sources"] = json.loads(record["sources"])
        return record

    def get_answer(self, cache_key: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM answers WHERE cache_key = ?", (cache_key,)).fetchone()
        if row is None or row["status"] not in SERVED_STATUSES:
            return None
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= now():
            return None
        return self._answer(row)

    def set_answer(self, cache_key: str, tier: str, product: dict, description: str, sources: list[str],
                   status: str = "active") -> None:
        check_answer(tier, status)
        created = now()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO answers (cache_key, tier, product, description, sources, "
                         "cached_at, status, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (cache_key, tier, json.dumps(product), description, json.dumps(sources),
                          created.isoformat(), status, _iso(expires_at(tier, created))))

    def delete_answer(self, cache_key: str) -> bool:
        with self._connect() as conn:
            return conn.execute("DELETE FROM answers WHERE cache_key = ?", (cache_key,)).rowcount > 0

    def list_answers(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM answers", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY cached_at DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._answer(r) for r in rows]

    # --- scan log, maintenance, backup ---

    def log_scan(self, record: dict) -> None:
        append_jsonl(config.RUN_LOG_PATH, record)

    def purge(self) -> dict:
        """Delete expired answers. (The local run log is the developer's own file.)"""
        with self._connect() as conn:
            n = conn.execute("DELETE FROM answers WHERE expires_at IS NOT NULL AND expires_at <= ?",
                             (now().isoformat(),)).rowcount
        return {"expired_answers_deleted": n}

    def export_answers(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM answers ORDER BY cache_key").fetchall()
        return [self._answer(r) for r in rows]

    def import_answers(self, rows: Iterable[dict]) -> int:
        count = 0
        with self._connect() as conn:
            for r in rows:
                check_answer(r["tier"], r["status"])
                conn.execute("INSERT OR REPLACE INTO answers (cache_key, tier, product, description, sources, "
                             "cached_at, status, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (r["cache_key"], r["tier"], json.dumps(r["product"]), r["description"],
                              json.dumps(r["sources"]), r["cached_at"], r["status"], r.get("expires_at")))
                count += 1
        return count

    def close(self) -> None:
        pass
