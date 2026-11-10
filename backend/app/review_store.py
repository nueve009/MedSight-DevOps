"""
The review console's data: the kb_* rows, decisions, evidence and FDA
candidates in Supabase, reached as the medsight_review login, plus the evidence
files in Supabase Storage.

Table and column names come only from TABLES below, and values are always bound
parameters. The rules that matter most are also in the database
(migrations/0005 and 0006), so a bug here can't approve a row they refuse.
"""
import logging
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import psycopg
import requests
from fastapi import Request
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import config
from app.kb_validate import INGREDIENT_FIELDS, PASSAGE_FIELDS, PRODUCT_FIELDS

log = logging.getLogger(__name__)

CHECKER_COLUMNS = {"checked_by", "description_checked_by"}
NULL_WHEN_EMPTY = {"reg_expiry", "portal_checked_on", "fda_reg_no"}   # dates, and the unique registration number


@dataclass(frozen=True)
class Table:
    api_name: str
    name: str
    key: str
    fields: tuple[str, ...]
    label: str   # the column shown in the queue

    @property
    def editable(self) -> tuple[str, ...]:
        """What a reviewer may type. Who entered or checked a row always comes from the signed-in account."""
        return tuple(f for f in self.fields if f not in CHECKER_COLUMNS and f != "entered_by" and f != self.key)


TABLES = {
    "products": Table("products", "kb_products", "product_id", tuple(PRODUCT_FIELDS), "brand_name"),
    "ingredients": Table("ingredients", "kb_ingredients", "generic_name", tuple(INGREDIENT_FIELDS), "generic_name"),
    "passages": Table("passages", "kb_passages", "passage_id", tuple(PASSAGE_FIELDS), "product_id"),
}


class StoreError(Exception):
    pass


class RowNotFound(StoreError):
    pass


class RuleRefused(StoreError):
    """The database refused the change, with a message written for the reviewer."""


class Conflict(StoreError):
    pass


class Unavailable(StoreError):
    pass


def _text(value) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _clean(row: dict, table: Optional[Table] = None) -> dict:
    """JSON-ready, and the dataset's own columns as strings, the way kb_validate reads them."""
    out = {k: _text(v) for k, v in row.items()}
    if table:
        for field in table.fields:
            out[field] = "" if out.get(field) is None else out[field]
    return out


class PostgresReviewStore:
    def __init__(self, conninfo: str):
        self.conninfo = conninfo

    @contextmanager
    def _conn(self):
        try:
            with psycopg.connect(self.conninfo, sslmode=config.DATABASE_SSLMODE, connect_timeout=5,
                                 prepare_threshold=None, row_factory=dict_row) as conn:
                yield conn
        except psycopg.errors.RaiseException as e:
            raise RuleRefused(e.diag.message_primary or "The database refused this change.") from e
        except psycopg.errors.UniqueViolation as e:
            raise Conflict("Another row already has that key or registration number.") from e
        except psycopg.errors.IntegrityError as e:
            raise RuleRefused("A value isn't allowed for this field, or a linked row doesn't exist.") from e
        except psycopg.errors.DataError as e:
            raise RuleRefused("A value has the wrong format (dates are YYYY-MM-DD).") from e
        except psycopg.OperationalError as e:
            log.error("review database unavailable", extra={"fields": {"error_class": type(e).__name__}})
            raise Unavailable("The database can't be reached right now.") from e

    @staticmethod
    def _select(table: Table, lock: bool = False) -> sql.Composed:
        query = sql.SQL("SELECT * FROM {} WHERE {} = %s").format(sql.Identifier(table.name), sql.Identifier(table.key))
        return query + sql.SQL(" FOR UPDATE") if lock else query

    def reviewer_name(self, user_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT name FROM reviewers WHERE user_id = %s AND active", (user_id,)).fetchone()
        return row["name"] if row else None

    def _summaries(self, tables: list[Table], where: sql.Composable, params: list, q: str, limit: int) -> list[dict]:
        rows = []
        with self._conn() as conn:
            for table in tables:
                query = sql.SQL(
                    "SELECT %s AS table_name, {key} AS key, {label} AS label, review_state, approved, entered_by, "
                    "last_edited_by, review_note, updated_at FROM {tbl} WHERE ({where}) "
                    "AND position(lower(%s) in lower({key} || ' ' || {label})) > 0 ORDER BY updated_at LIMIT %s"
                ).format(key=sql.Identifier(table.key), label=sql.Identifier(table.label),
                         tbl=sql.Identifier(table.name), where=where)
                found = conn.execute(query, [table.api_name, *params, q, limit]).fetchall()
                rows += [_clean(r) for r in found]
        return rows

    def queue(self, table: Optional[Table], q: str, actor: str, limit: int = 200) -> list[dict]:
        where = sql.SQL("review_state = 'pending' AND lower(btrim(entered_by)) <> lower(btrim(%s)) "
                        "AND lower(btrim(last_edited_by)) <> lower(btrim(%s))")
        return self._summaries([table] if table else list(TABLES.values()), where, [actor, actor], q, limit)

    def mine(self, actor: str, limit: int = 200) -> list[dict]:
        where = sql.SQL("lower(btrim(entered_by)) = lower(btrim(%s)) "
                        "OR lower(btrim(last_edited_by)) = lower(btrim(%s))")
        return self._summaries(list(TABLES.values()), where, [actor, actor], "", limit)

    def get_row(self, table: Table, key: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(self._select(table), (key,)).fetchone()
        return _clean(row, table) if row else None

    def history(self, table: Table, key: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT reviewer, decision, note, checklist, created_at FROM kb_reviews "
                                "WHERE table_name = %s AND row_key = %s ORDER BY created_at, id",
                                (table.name, key)).fetchall()
        return [_clean(r) for r in rows]

    def evidence(self, table: Table, key: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id, kind, storage_path, uploaded_by, created_at FROM kb_evidence "
                                "WHERE table_name = %s AND row_key = %s ORDER BY created_at, id",
                                (table.name, key)).fetchall()
        return [_clean(r) for r in rows]

    def add_evidence(self, table: Table, key: str, kind: str, storage_path: str, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT INTO kb_evidence (table_name, row_key, kind, storage_path, uploaded_by) "
                         "VALUES (%s, %s, %s, %s, %s)", (table.name, key, kind, storage_path, actor))

    @staticmethod
    def _value(column: str, value: str) -> Optional[str]:
        return None if column in NULL_WHEN_EMPTY and not value else value

    def add_row(self, table: Table, values: dict, actor: str) -> None:
        columns = [c for c in table.fields if c not in CHECKER_COLUMNS]
        data = {**values, "entered_by": actor}
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table.name), sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() * len(columns)))
        with self._conn() as conn:
            conn.execute(query, [self._value(c, data.get(c, "")) for c in columns])

    def update_row(self, table: Table, key: str, changes: dict, actor: str) -> None:
        columns = [c for c in changes if c in table.editable]
        sets = [sql.SQL("{} = %s").format(sql.Identifier(c)) for c in [*columns, "last_edited_by"]]
        query = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
            sql.Identifier(table.name), sql.SQL(", ").join(sets), sql.Identifier(table.key))
        with self._conn() as conn:
            cur = conn.execute(query, [*(self._value(c, changes[c]) for c in columns), actor, key])
            if cur.rowcount == 0:
                raise RowNotFound()

    @staticmethod
    def _record(conn, table: Table, key: str, actor: str, decision: str, checklist: dict, note: str, row: dict) -> None:
        conn.execute("INSERT INTO kb_reviews (table_name, row_key, reviewer, decision, checklist, note, row_snapshot) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (table.name, key, actor, decision, Jsonb(checklist), note, Jsonb(_clean(row))))

    def approve(self, table: Table, key: str, actor: str, checklist: dict, version: str) -> None:
        """The decision and the approval together, or neither. version is the row the reviewer actually read."""
        with self._conn() as conn, conn.transaction():
            row = conn.execute(self._select(table, lock=True), (key,)).fetchone()
            if row is None:
                raise RowNotFound()
            if _text(row["updated_at"]) != version:
                raise Conflict("This row changed while you were reading it. Open it again.")
            if row["approved"]:
                raise Conflict("This row is already approved.")
            self._record(conn, table, key, actor, "approve", checklist, "", row)
            sets = ["checked_by", "description_checked_by"] if table.name == "kb_products" else ["checked_by"]
            query = sql.SQL("UPDATE {} SET {}, approved = true WHERE {} = %s").format(
                sql.Identifier(table.name),
                sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in sets),
                sql.Identifier(table.key))
            conn.execute(query, [*([actor] * len(sets)), key])

    def request_changes(self, table: Table, key: str, actor: str, note: str) -> None:
        with self._conn() as conn, conn.transaction():
            row = conn.execute(self._select(table, lock=True), (key,)).fetchone()
            if row is None:
                raise RowNotFound()
            if row["approved"]:
                raise Conflict("This row is approved. Edit it to send it back for review.")
            self._record(conn, table, key, actor, "request_changes", {}, note, row)
            query = sql.SQL("UPDATE {} SET review_state = 'changes_requested', review_note = %s WHERE {} = %s").format(
                sql.Identifier(table.name), sql.Identifier(table.key))
            conn.execute(query, (note, key))

    def delete_row(self, table: Table, key: str, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("SELECT kb_delete_unapproved(%s, %s, %s)", (table.name, key, actor))

    def approval_context(self, table: Table, key: str) -> dict:
        """The other rows a row's rules look at (a product's ingredients and passages, a passage's product)."""
        context = {"ingredients": [], "passages": [], "product": None}
        with self._conn() as conn:
            if table.api_name == "products":
                context["ingredients"] = [_clean(r, TABLES["ingredients"])
                                          for r in conn.execute("SELECT * FROM kb_ingredients").fetchall()]
                context["passages"] = [_clean(r, TABLES["passages"]) for r in conn.execute(
                    "SELECT * FROM kb_passages WHERE product_id = %s", (key,)).fetchall()]
            elif table.api_name == "passages":
                row = conn.execute(self._select(TABLES["passages"]), (key,)).fetchone()
                product = row and conn.execute(self._select(TABLES["products"]), (row["product_id"],)).fetchone()
                context["product"] = _clean(product, TABLES["products"]) if product else None
        return context

    def fda_candidates(self, brand: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM kb_fda_lookups WHERE lower(brand_query) = lower(%s) "
                                "OR lower(brand_name) = lower(%s) ORDER BY brand_name, strength, fda_reg_no",
                                (brand, brand)).fetchall()
        return [_clean(r) for r in rows]

    def fda_candidate(self, candidate_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM kb_fda_lookups WHERE id = %s", (candidate_id,)).fetchone()
        return _clean(row) if row else None

    def stats(self) -> dict:
        out: dict = {}
        with self._conn() as conn:
            for table in TABLES.values():
                query = sql.SQL("SELECT review_state, count(*) AS n FROM {} GROUP BY review_state").format(
                    sql.Identifier(table.name))
                counts = {"pending": 0, "changes_requested": 0, "approved": 0}
                counts.update({r["review_state"]: r["n"] for r in conn.execute(query).fetchall()})
                out[table.api_name] = counts
            out["fda_candidates"] = conn.execute("SELECT count(*) AS n FROM kb_fda_lookups").fetchone()["n"]
        return out


class SupabaseStorage:
    """Evidence files. Every call uses the reviewer's own token, so Storage applies their permissions."""

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "apikey": config.SUPABASE_ANON_KEY}

    @staticmethod
    def _object_url(kind: str, path: str) -> str:
        return (f"{config.SUPABASE_URL}/storage/v1/object/{kind}{config.EVIDENCE_BUCKET}/"
                f"{urllib.parse.quote(path, safe='/')}")

    def upload(self, token: str, path: str, data: bytes, content_type: str) -> None:
        try:
            resp = requests.post(self._object_url("", path), data=data, timeout=30,
                                 headers={**self._headers(token), "Content-Type": content_type, "x-upsert": "false"})
        except requests.RequestException as e:
            raise Unavailable("Supabase Storage can't be reached right now.") from e
        if resp.status_code not in (200, 201):
            log.error("evidence upload failed", extra={"fields": {"status": resp.status_code}})
            raise Unavailable("Supabase Storage refused the upload. Check the kb-evidence bucket setup.")

    def signed_url(self, token: str, path: str, seconds: int) -> Optional[str]:
        """A short-lived link, or None if it can't be made (the page shows the file as unavailable)."""
        try:
            resp = requests.post(self._object_url("sign/", path), json={"expiresIn": seconds}, timeout=10,
                                 headers=self._headers(token))
            if resp.status_code != 200:
                return None
            return f"{config.SUPABASE_URL}/storage/v1{resp.json()['signedURL']}"
        except (requests.RequestException, ValueError, KeyError):
            return None


def get_store(request: Request):
    """The store for this app; tests put a fake in app.state.review_store."""
    state = request.app.state
    store = getattr(state, "review_store", None)
    if store is None:
        if not config.REVIEW_DATABASE_URL:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="The review console isn't configured (REVIEW_DATABASE_URL).")
        store = state.review_store = PostgresReviewStore(config.REVIEW_DATABASE_URL)
    return store


def get_storage(request: Request):
    state = request.app.state
    storage = getattr(state, "review_storage", None)
    if storage is None:
        storage = state.review_storage = SupabaseStorage()
    return storage
