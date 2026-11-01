"""
The cache and scan log, behind one interface with two versions:

- SQLiteStore   (store/sqlite.py)    tests, laptops and eval_kb.py; the scan log
                                     goes to logs/run_log.jsonl
- PostgresStore (store/postgres.py)  Railway with Supabase; tables come from
                                     migrations/ (scripts/migrate.py)

make_store() picks one from CACHE_BACKEND ("sqlite", the default, or "postgres").

What is stored:
- parses:   the raw-text parse cache, keyed by utils.raw_text_key
- answers:  general (and, from M6, web) descriptions, keyed by utils.answer_key.
            Reviewed and ingredient answers are never stored: they come from
            kb/ in memory.
- scan log: one record per scan
- rate counters: fixed-window counts for app/ratelimit.py

Answer status: "active" and "pending_review" are served, "rejected" is a miss
(searched again on the next scan). An answer past its expires_at is a miss.
Web answers expire after config.WEB_ANSWER_TTL_DAYS; general ones don't.

Every method may raise. The pipeline treats any error as a miss or a skipped
write (rule 4: the cache fails open), so a database problem never fails a scan.
"""
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol

from app import config

SERVED_STATUSES = ("active", "pending_review")
ANSWER_STATUSES = ("active", "pending_review", "rejected")


class StoreUnavailable(Exception):
    """The database can't be reached, or is being skipped after a failure."""


class StoreSkipped(StoreUnavailable):
    """The database is being skipped after a recent failure (already logged once)."""


class Store(Protocol):
    def get_parse(self, cache_key: str) -> Optional[dict]: ...
    def set_parse(self, cache_key: str, result: dict) -> None: ...
    def get_answer(self, cache_key: str) -> Optional[dict]: ...
    def set_answer(self, cache_key: str, tier: str, product: dict, description: str, sources: list[str],
                   status: str = "active") -> None: ...
    def delete_answer(self, cache_key: str) -> bool: ...
    def list_answers(self, status: Optional[str] = None, limit: int = 100) -> list[dict]: ...
    def log_scan(self, record: dict) -> None: ...
    def incr_counters(self, items: list[tuple[str, int, int]]) -> list[int]: ...
    def purge(self) -> dict: ...
    def export_answers(self) -> list[dict]: ...
    def import_answers(self, rows: Iterable[dict]) -> int: ...
    def close(self) -> None: ...


def now() -> datetime:
    return datetime.now(timezone.utc)


def expires_at(tier: str, created: datetime) -> Optional[datetime]:
    """When an answer of this tier stops being served (None = never)."""
    return created + timedelta(days=config.WEB_ANSWER_TTL_DAYS) if tier == "web" else None


def check_answer(tier: str, status: str) -> None:
    if tier not in ("general", "web"):
        raise ValueError(f"tier {tier!r} is never cached")   # reviewed and ingredient come from kb/
    if status not in ANSWER_STATUSES:
        raise ValueError(f"unknown answer status {status!r}")


def make_store() -> Store:
    backend = config.CACHE_BACKEND
    if backend == "sqlite":
        from app.store.sqlite import SQLiteStore
        return SQLiteStore()
    if backend == "postgres":
        if not config.DATABASE_URL:
            # A configuration mistake, not an outage: refuse to start.
            raise RuntimeError("CACHE_BACKEND=postgres needs DATABASE_URL")
        from app.store.postgres import PostgresStore
        return PostgresStore(config.DATABASE_URL)
    raise RuntimeError(f"CACHE_BACKEND must be 'sqlite' or 'postgres', not {backend!r}")
