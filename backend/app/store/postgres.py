"""
Postgres (Supabase) version of the store (see store/__init__.py). Tables are
created by migrations/ (python -m scripts.migrate), never by this module.

Fails open, fast:
- connecting gives up after config.DB_CONNECT_TIMEOUT_S, each query after
  config.DB_QUERY_TIMEOUT_MS
- after a failure the database is skipped for config.DB_SKIP_AFTER_FAILURE_S
  (circuit breaker), so an outage costs one slow scan, not every scan
Every failure raises StoreUnavailable; the pipeline treats it as a miss.

Works through Supabase's session pooler or a direct connection. Prepared
statements are off, so the transaction pooler (port 6543) works too.
"""
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Iterable, Optional, TypeVar

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from app import config
from app.logging_setup import request_id_var
from app.store import SERVED_STATUSES, StoreSkipped, StoreUnavailable, check_answer, check_status, expires_at, now

log = logging.getLogger(__name__)
T = TypeVar("T")

# Scan-log columns copied out of the pipeline's record (the rest stays in `record`).
_SCAN_COLUMNS = ("status", "rescan_reason", "tier", "cache", "parse_cache", "match", "matched_product_id",
                 "kb_version", "client", "platform", "app_version")


class CircuitBreaker:
    """After a failure, refuse calls for `cooldown_s` seconds."""

    def __init__(self, cooldown_s: float):
        self.cooldown_s = cooldown_s
        self._open_until = 0.0
        self._lock = threading.Lock()

    def check(self) -> None:
        if time.monotonic() < self._open_until:
            raise StoreSkipped("database skipped after a recent failure")

    def trip(self, error: BaseException) -> None:
        with self._lock:
            already_open = time.monotonic() < self._open_until
            self._open_until = time.monotonic() + self.cooldown_s
        if not already_open:
            log.warning("database skipped", extra={"fields": {
                "error_class": type(error).__name__, "skip_s": self.cooldown_s}})


def _answer(row: dict) -> dict:
    return {"cache_key": row["cache_key"], "tier": row["tier"], "product": row["product"],
            "description": row["description"], "sources": row["source_urls"], "status": row["status"],
            "cached_at": row["created_at"].isoformat(),
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            "page_text": row["page_text"]}


def _audit_row(row: dict) -> dict:
    return {"id": row["id"], "created_at": row["created_at"].isoformat(), "admin": row["admin"],
            "action": row["action"], "cache_key": row["cache_key"], "before": row["before"], "after": row["after"]}


def _json(answer: Optional[dict]) -> Optional[Jsonb]:
    return Jsonb(answer) if answer is not None else None


class PostgresStore:
    def __init__(self, conninfo: str):
        self.breaker = CircuitBreaker(config.DB_SKIP_AFTER_FAILURE_S)
        self.pool = ConnectionPool(
            conninfo, min_size=0, max_size=config.DB_POOL_MAX, open=False, timeout=config.DB_CONNECT_TIMEOUT_S,
            kwargs={"connect_timeout": max(1, round(config.DB_CONNECT_TIMEOUT_S)), "sslmode": config.DATABASE_SSLMODE,
                    "autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        )
        self.pool.open(wait=False)   # never block startup on the database

    def _run(self, fn: Callable[[psycopg.Cursor], T]) -> T:
        self.breaker.check()
        try:
            with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(config.DB_QUERY_TIMEOUT_MS)}")
                return fn(cur)
        except (psycopg.Error, PoolTimeout) as e:
            self.breaker.trip(e)
            raise StoreUnavailable(type(e).__name__) from e

    # --- parses ---

    def get_parse(self, cache_key: str) -> Optional[dict]:
        def q(cur):
            cur.execute("SELECT result FROM parses WHERE cache_key = %s", (cache_key,))
            row = cur.fetchone()
            return row["result"] if row else None
        return self._run(q)

    def set_parse(self, cache_key: str, result: dict) -> None:
        self._run(lambda cur: cur.execute(
            "INSERT INTO parses (cache_key, result) VALUES (%s, %s) "
            "ON CONFLICT (cache_key) DO UPDATE SET result = EXCLUDED.result, cached_at = now()",
            (cache_key, Jsonb(result))))

    # --- answers ---

    def get_answer(self, cache_key: str) -> Optional[dict]:
        def q(cur):
            cur.execute("SELECT * FROM answers WHERE cache_key = %s AND status = ANY(%s) "
                        "AND (expires_at IS NULL OR expires_at > now())", (cache_key, list(SERVED_STATUSES)))
            row = cur.fetchone()
            return _answer(row) if row else None
        return self._run(q)

    def set_answer(self, cache_key: str, tier: str, product: dict, description: str, sources: list[str],
                   status: str = "active", page_text: str = "") -> None:
        check_answer(tier, status)
        created = now()
        self._run(lambda cur: cur.execute(
            "INSERT INTO answers (cache_key, tier, product, description, source_urls, status, created_at, expires_at, "
            "page_text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (cache_key) DO UPDATE SET "
            "tier = EXCLUDED.tier, product = EXCLUDED.product, description = EXCLUDED.description, "
            "source_urls = EXCLUDED.source_urls, status = EXCLUDED.status, created_at = EXCLUDED.created_at, "
            "expires_at = EXCLUDED.expires_at, page_text = EXCLUDED.page_text",
            (cache_key, tier, Jsonb(product), description, Jsonb(sources), status, created,
             expires_at(tier, created), page_text)))

    def delete_answer(self, cache_key: str) -> bool:
        def q(cur):
            cur.execute("DELETE FROM answers WHERE cache_key = %s", (cache_key,))
            return cur.rowcount > 0
        return self._run(q)

    def list_answers(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        def q(cur):
            if status:
                cur.execute("SELECT * FROM answers WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                            (status, limit))
            else:
                cur.execute("SELECT * FROM answers ORDER BY created_at DESC LIMIT %s", (limit,))
            return [_answer(r) for r in cur.fetchall()]
        return self._run(q)

    # --- scan log, maintenance, backup ---

    def log_scan(self, record: dict) -> None:
        rest = {k: v for k, v in record.items() if k != "raw_ocr_text"}
        self._run(lambda cur: cur.execute(
            f"INSERT INTO scan_log (request_id, device_hash, ocr_text, {', '.join(_SCAN_COLUMNS)}, record) "
            f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(_SCAN_COLUMNS))}, %s)",
            (request_id_var.get(), record.get("device"), record.get("raw_ocr_text"),
             *(record.get(c) for c in _SCAN_COLUMNS), Jsonb(rest))))

    def incr_counters(self, items: list[tuple[str, int, int]]) -> list[int]:
        """Add 1 to each (bucket, window_start, window_s) counter in one statement; return the new counts."""
        def q(cur):
            cur.execute(
                "INSERT INTO rate_limits (bucket, window_start, window_s, count) VALUES "
                + ", ".join(["(%s, %s, %s, 1)"] * len(items))
                + " ON CONFLICT (bucket, window_start) DO UPDATE SET count = rate_limits.count + 1 "
                  "RETURNING bucket, window_start, count",
                [v for item in items for v in item])
            counts = {(r["bucket"], r["window_start"]): r["count"] for r in cur.fetchall()}
            return [counts[(b, s)] for b, s, _ in items]
        return self._run(q)

    def purge(self) -> dict:
        """Delete expired answers and finished rate windows, and clear OCR text in
        the scan log older than config.SCAN_LOG_OCR_RETENTION_DAYS (the rest of
        each row stays)."""
        def q(cur):
            cur.execute("DELETE FROM answers WHERE expires_at IS NOT NULL AND expires_at <= now()")
            expired = cur.rowcount
            cur.execute("DELETE FROM rate_limits WHERE window_start + window_s < extract(epoch FROM now())")
            windows = cur.rowcount
            cur.execute("UPDATE scan_log SET ocr_text = NULL WHERE ocr_text IS NOT NULL "
                        "AND created_at < now() - make_interval(days => %s)",
                        (config.SCAN_LOG_OCR_RETENTION_DAYS,))
            return {"expired_answers_deleted": expired, "rate_windows_deleted": windows,
                    "scan_log_ocr_text_cleared": cur.rowcount}
        return self._run(q)

    def export_answers(self) -> list[dict]:
        def q(cur):
            cur.execute("SELECT * FROM answers ORDER BY cache_key")
            return [_answer(r) for r in cur.fetchall()]
        return self._run(q)

    def import_answers(self, rows: Iterable[dict]) -> int:
        rows = list(rows)
        for r in rows:
            check_answer(r["tier"], r["status"])

        def q(cur):
            for r in rows:
                cur.execute(
                    "INSERT INTO answers (cache_key, tier, product, description, source_urls, status, created_at, "
                    "expires_at, page_text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (cache_key) DO UPDATE SET "
                    "tier = EXCLUDED.tier, product = EXCLUDED.product, description = EXCLUDED.description, "
                    "source_urls = EXCLUDED.source_urls, status = EXCLUDED.status, "
                    "created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at, "
                    "page_text = EXCLUDED.page_text",
                    (r["cache_key"], r["tier"], Jsonb(r["product"]), r["description"], Jsonb(r["sources"]),
                     r["status"], datetime.fromisoformat(r["cached_at"]),
                     datetime.fromisoformat(r["expires_at"]) if r.get("expires_at") else None,
                     r.get("page_text", "")))
            return len(rows)
        return self._run(q)

    # --- admin (app/admin.py): each write and its audit row in one transaction ---

    def read_answer(self, cache_key: str) -> Optional[dict]:
        def q(cur):
            cur.execute("SELECT * FROM answers WHERE cache_key = %s", (cache_key,))
            row = cur.fetchone()
            return _answer(row) if row else None
        return self._run(q)

    @staticmethod
    def _audit(cur, admin: str, action: str, cache_key: str, before: dict, after: Optional[dict]) -> None:
        cur.execute("INSERT INTO audit_log (admin, action, cache_key, before, after) VALUES (%s, %s, %s, %s, %s)",
                    (admin, action, cache_key, Jsonb(before), _json(after)))

    def admin_update_answer(self, cache_key: str, admin: str, status: Optional[str] = None,
                            description: Optional[str] = None) -> Optional[tuple[dict, dict]]:
        check_status(status)

        def q(cur):
            cur.execute("SELECT * FROM answers WHERE cache_key = %s FOR UPDATE", (cache_key,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("UPDATE answers SET status = COALESCE(%s, status), description = COALESCE(%s, description) "
                        "WHERE cache_key = %s RETURNING *", (status, description, cache_key))
            before, after = _answer(row), _answer(cur.fetchone())
            self._audit(cur, admin, "update", cache_key, before, after)
            return before, after
        return self._run(q)

    def admin_delete_answer(self, cache_key: str, admin: str) -> Optional[dict]:
        def q(cur):
            cur.execute("DELETE FROM answers WHERE cache_key = %s RETURNING *", (cache_key,))
            row = cur.fetchone()
            if row is None:
                return None
            before = _answer(row)
            self._audit(cur, admin, "delete", cache_key, before, None)
            return before
        return self._run(q)

    def list_audit(self, limit: int = 100) -> list[dict]:
        def q(cur):
            cur.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,))
            return [_audit_row(r) for r in cur.fetchall()]
        return self._run(q)

    def export_audit(self) -> list[dict]:
        def q(cur):
            cur.execute("SELECT * FROM audit_log ORDER BY id")
            return [_audit_row(r) for r in cur.fetchall()]
        return self._run(q)

    def close(self) -> None:
        self.pool.close()
