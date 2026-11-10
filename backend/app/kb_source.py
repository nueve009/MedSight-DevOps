"""
The reviewed dataset from Supabase (KB_BACKEND=postgres): the approved rows of
kb_products, kb_ingredients and kb_passages (migrations/0005_kb_dataset.sql).

Teammates add rows in the Supabase Table Editor and a second teammate approves
each one there. The backend reads only approved rows: the backend role's RLS
policies hide the rest, and the query asks for approved rows too. Every
approved row is checked again with the dataset rules (app/kb_validate.py);
one that breaks a rule is dropped and logged, never spoken.

KBRefresher loads the dataset at startup and again every KB_REFRESH_S
seconds, so an approval goes live without a redeploy. A failed load keeps
the dataset the server already has (empty only if it never loaded, which is
the same as a missing kb.sqlite: every scan falls to the warned tiers).
"""
import hashlib
import json
import logging
import threading
from datetime import date
from typing import Callable, Optional

from app import config
from app.kb_validate import INGREDIENT_FIELDS, PASSAGE_FIELDS, PRODUCT_FIELDS, Rows, servable
from app.retrieval import KnowledgeBase

log = logging.getLogger(__name__)

# name in the dataset dict -> (table, columns, key column)
KB_TABLES = {
    "ingredients": ("kb_ingredients", INGREDIENT_FIELDS, "generic_name"),
    "products": ("kb_products", PRODUCT_FIELDS, "product_id"),
    "passages": ("kb_passages", PASSAGE_FIELDS, "passage_id"),
}
LOAD_TIMEOUT_MS = 10_000


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def kb_conninfo() -> str:
    return config.KB_DATABASE_URL or config.DATABASE_URL


def fetch_approved(conninfo: str) -> dict[str, Rows]:
    """Every approved row, read in one consistent snapshot, as CSV-style string dicts."""
    import psycopg
    from psycopg.rows import dict_row

    tables: dict[str, Rows] = {}
    with psycopg.connect(conninfo, sslmode=config.DATABASE_SSLMODE,
                         connect_timeout=max(2, round(config.DB_CONNECT_TIMEOUT_S)),
                         prepare_threshold=None, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = {LOAD_TIMEOUT_MS}")
        for name, (table, fields, key) in KB_TABLES.items():
            cur = conn.execute(f"SELECT {', '.join(fields)} FROM {table} WHERE approved ORDER BY {key}")
            tables[name] = [(f"{table}:{row[key]}", {f: _text(row[f]) for f in fields}) for row in cur]
        conn.rollback()
    return tables


def dataset_version(data: dict) -> Optional[str]:
    """Hash of the rows served; changes whenever an approval or edit changes them. None when empty."""
    if not data["products"] and not data["ingredients"]:
        return None
    blob = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def servable_from_postgres(conninfo: str) -> tuple[dict, Optional[str], list[str]]:
    """(rows to serve, their version, the errors that dropped approved rows)."""
    raw = fetch_approved(conninfo)
    # An approved passage whose product is waiting for (re-)approval is expected
    # after an edit, not a broken row: skip it without reporting it.
    approved_products = {row["product_id"] for _, row in raw["products"]}
    passages = [(where, row) for where, row in raw["passages"] if row["product_id"] in approved_products]
    data, dropped = servable(raw["products"], raw["ingredients"], passages)
    return data, dataset_version(data), dropped


def load_from_postgres(conninfo: str) -> tuple[KnowledgeBase, list[str]]:
    data, version, dropped = servable_from_postgres(conninfo)
    return KnowledgeBase.from_rows(data["products"], data["ingredients"], data["passages"], version), dropped


class KBRefresher:
    """Keeps state.kb up to date: one load now, then one every interval_s in a background thread."""

    def __init__(self, state, loader: Callable[[], tuple[KnowledgeBase, list[str]]], interval_s: float):
        self.state = state
        self.loader = loader
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._failing = False

    def load_once(self) -> bool:
        try:
            kb, dropped = self.loader()
        except Exception as e:   # any failure keeps the current dataset
            if not self._failing:
                log.error("kb load failed; keeping the current dataset", extra={"fields": {
                    "error_class": type(e).__name__, "kb_version": self.state.kb.version,
                    "kb_products": len(self.state.kb.products)}})
            self._failing = True
            return False
        if self._failing:
            log.info("kb load working again")
        self._failing = False
        if kb.version != self.state.kb.version:
            self.state.kb = kb   # one assignment: a scan sees the old dataset or the new one, never a mix
            log.info("kb loaded", extra={"fields": {"kb_version": kb.version, "kb_products": len(kb.products),
                                                    "kb_ingredients": len(kb.ingredients),
                                                    "rows_dropped": len(dropped)}})
            for error in dropped:
                log.warning("kb row not served", extra={"fields": {"error": error}})
        return True

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.load_once()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kb-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
