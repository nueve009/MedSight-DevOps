"""The cache store: one suite run on SQLite and, when a test database is given,
on Postgres. Set MEDSIGHT_TEST_DATABASE_URL to a throwaway database (its tables
are dropped and re-created) to run the Postgres half; CI does this with a
service container. MEDSIGHT_TEST_DATABASE_SSLMODE defaults to "disable"."""
import json
import os
import time

import pytest

from app import config, description_agent, llm_parse
from app.pipeline import ScanRequest, identify_scan
from app.store import StoreSkipped, StoreUnavailable, make_store
from app.store.sqlite import SQLiteStore
from scripts import migrate, store_admin
from tests.fixtures import labels

PG_URL = os.environ.get("MEDSIGHT_TEST_DATABASE_URL", "")
PG_SSLMODE = os.environ.get("MEDSIGHT_TEST_DATABASE_SSLMODE", "disable")
PRODUCT = {"brand": "Biogesic", "generics": ["Paracetamol"], "strengths": ["500 mg"]}
# Nothing listens on port 1, so connecting fails at once.
DEAD_URL = "postgresql://nobody:nothing@127.0.0.1:1/none"


def _reset_postgres() -> None:
    import psycopg
    with psycopg.connect(PG_URL, sslmode=PG_SSLMODE, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS parses, answers, scan_log, rate_limits, audit_log, schema_version, "
                     "kb_passages, kb_products, kb_ingredients, kb_review_log CASCADE")
        conn.execute("DROP FUNCTION IF EXISTS kb_contains_dosing, kb_split, kb_check_spoken, kb_check_approvable, "
                     "kb_content, kb_review_guard, kb_review_audit CASCADE")
        conn.execute(migrate._BOOTSTRAP)
        for version, path in migrate.migration_files():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version, name) VALUES (%s, %s)", (version, path.name))


def pg_execute(sql: str, *args):
    import psycopg
    with psycopg.connect(PG_URL, sslmode=PG_SSLMODE, autocommit=True) as conn:
        cur = conn.execute(sql, args)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def pg_store(monkeypatch):
    if not PG_URL:
        pytest.skip("MEDSIGHT_TEST_DATABASE_URL not set")
    from app.store.postgres import PostgresStore
    monkeypatch.setattr(config, "DATABASE_SSLMODE", PG_SSLMODE)
    _reset_postgres()
    s = PostgresStore(PG_URL)
    yield s
    s.close()


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "cache.sqlite")
    return request.getfixturevalue("pg_store")


# --- the same behavior on both ---

def test_parse_round_trip(store):
    assert store.get_parse("k") is None
    store.set_parse("k", {"products": [PRODUCT], "valid": True})
    store.set_parse("k", {"products": [PRODUCT], "valid": True, "confidence": 0.9})
    assert store.get_parse("k") == {"products": [PRODUCT], "valid": True, "confidence": 0.9}


def test_answer_round_trip_and_replace(store):
    assert store.get_answer("k") is None
    store.set_answer("k", "general", PRODUCT, "A pain reliever.", [])
    store.set_answer("k", "general", PRODUCT, "A pain reliever and fever reducer.", ["https://x.test/a"])
    a = store.get_answer("k")
    assert (a["tier"], a["description"], a["sources"], a["product"], a["status"], a["expires_at"]) == \
        ("general", "A pain reliever and fever reducer.", ["https://x.test/a"], PRODUCT, "active", None)


def test_web_answers_expire(store, monkeypatch):
    store.set_answer("fresh", "web", PRODUCT, "From the web.", ["https://x.test"])
    assert store.get_answer("fresh")["expires_at"] is not None
    monkeypatch.setattr(config, "WEB_ANSWER_TTL_DAYS", -1)   # written already expired
    store.set_answer("old", "web", PRODUCT, "From the web.", ["https://x.test"])
    assert store.get_answer("old") is None
    assert store.purge()["expired_answers_deleted"] == 1
    assert [a["cache_key"] for a in store.list_answers()] == ["fresh"]


def test_rejected_answers_are_a_miss_pending_ones_are_served(store):
    store.set_answer("pending", "web", PRODUCT, "From the web.", ["https://x.test"], status="pending_review")
    store.set_answer("rejected", "web", PRODUCT, "From the web.", ["https://x.test"], status="rejected")
    assert store.get_answer("pending")["status"] == "pending_review"
    assert store.get_answer("rejected") is None
    assert [a["cache_key"] for a in store.list_answers(status="rejected")] == ["rejected"]


@pytest.mark.parametrize("tier, status", [("reviewed", "active"), ("ingredient", "active"), ("web", "verified")])
def test_reviewed_answers_and_unknown_statuses_are_never_stored(store, tier, status):
    with pytest.raises(ValueError):
        store.set_answer("k", tier, PRODUCT, "x", [], status=status)


def test_delete_answer(store):
    store.set_answer("k", "general", PRODUCT, "A pain reliever.", [])
    assert store.delete_answer("k") is True
    assert store.delete_answer("k") is False
    assert store.get_answer("k") is None


def test_admin_update_writes_the_audit_log_in_the_same_step(store):
    store.set_answer("w", "web", PRODUCT, "From the web.", ["https://x.test"], status="pending_review")
    before, after = store.admin_update_answer("w", "mon", status="active")
    assert (before["status"], after["status"]) == ("pending_review", "active")
    assert after["description"] == "From the web." and after["tier"] == "web"
    _, edited = store.admin_update_answer("w", "ana", description="Relieves pain and fever.")
    assert (edited["status"], edited["description"]) == ("active", "Relieves pain and fever.")
    audit = store.list_audit()
    assert [(a["admin"], a["action"], a["cache_key"]) for a in audit] == [("ana", "update", "w"),
                                                                           ("mon", "update", "w")]
    assert audit[1]["before"]["status"] == "pending_review" and audit[1]["after"]["status"] == "active"


def test_admin_delete_and_missing_rows(store):
    store.set_answer("g", "general", PRODUCT, "General.", [])
    assert store.admin_update_answer("nope", "mon", status="rejected") is None
    assert store.admin_delete_answer("nope", "mon") is None
    assert store.admin_delete_answer("g", "mon")["description"] == "General."
    assert store.read_answer("g") is None
    (entry,) = store.list_audit()
    assert (entry["action"], entry["after"], entry["before"]["cache_key"]) == ("delete", None, "g")


def test_admin_reads_rejected_and_expired_rows(store, monkeypatch):
    store.set_answer("r", "web", PRODUCT, "Rejected.", ["https://x.test"], status="rejected")
    monkeypatch.setattr(config, "WEB_ANSWER_TTL_DAYS", -1)
    store.set_answer("e", "web", PRODUCT, "Expired.", ["https://x.test"])
    assert store.get_answer("r") is None and store.get_answer("e") is None
    assert store.read_answer("r")["status"] == "rejected"
    assert store.read_answer("e")["description"] == "Expired."


def test_admin_update_refuses_unknown_statuses(store):
    store.set_answer("w", "web", PRODUCT, "From the web.", ["https://x.test"])
    with pytest.raises(ValueError):
        store.admin_update_answer("w", "mon", status="verified")
    assert store.list_audit() == []


def test_export_includes_the_audit_log(store, tmp_path):
    store.set_answer("g", "general", PRODUCT, "General.", [])
    store.admin_update_answer("g", "mon", status="rejected")
    store_admin.export(store, tmp_path / "exports")
    (audit_file,) = (tmp_path / "exports").glob("audit-*.jsonl")
    rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert [(r["admin"], r["action"]) for r in rows] == [("mon", "update")]


def test_export_and_restore(store, tmp_path):
    store.set_answer("a", "general", PRODUCT, "A pain reliever.", [])
    store.set_answer("b", "web", PRODUCT, "From the web.", ["https://x.test"], status="pending_review")
    path = store_admin.export(store, tmp_path / "exports")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    store.delete_answer("a")
    store.set_answer("b", "general", PRODUCT, "Changed after the export.", [])
    assert store_admin.restore(store, path) == 2
    assert store.get_answer("a")["description"] == "A pain reliever."
    b = store.get_answer("b")
    assert (b["tier"], b["status"], b["description"]) == ("web", "pending_review", "From the web.")


def test_rate_counters(store):
    assert store.incr_counters([("device:a/60", 0, 60), ("device:a/86400", 0, 86400)]) == [1, 1]
    assert store.incr_counters([("device:a/60", 0, 60), ("device:a/86400", 0, 86400)]) == [2, 2]
    assert store.incr_counters([("device:a/60", 60, 60)]) == [1]          # the next window starts at 1
    assert store.purge()["rate_windows_deleted"] == 3                      # windows long finished
    assert store.incr_counters([("device:a/60", 0, 60)]) == [1]


def test_the_pipeline_uses_the_store(store, fake_llm):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)
    assert identify_scan(ScanRequest(labels.BIOGESIC_TEXT), store)["cache"] == "miss"
    assert identify_scan(ScanRequest(labels.BIOGESIC_TEXT), store)["cache"] == "hit"
    assert fake_llm.count(llm_parse.PARSE_SYSTEM_PROMPT) == 1


# --- the scan log ---

def test_sqlite_scan_log_is_the_local_run_log(fake_llm, cache):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)
    identify_scan(ScanRequest(labels.BIOGESIC_TEXT, meta={"device": "hashed"}), cache)
    line = json.loads(config.RUN_LOG_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert (line["status"], line["device"], line["raw_ocr_text"]) == ("ok", "hashed", labels.BIOGESIC_TEXT)


def test_postgres_scan_log_keeps_ocr_text_out_of_the_record_and_purges_it(pg_store, fake_llm):
    store = pg_store
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)
    identify_scan(ScanRequest(labels.BIOGESIC_TEXT, meta={"device": "hashed"}), store)
    (row,) = pg_execute("SELECT device_hash, status, tier, ocr_text, record FROM scan_log")
    assert row[:4] == ("hashed", "ok", "general", labels.BIOGESIC_TEXT)
    assert "raw_ocr_text" not in row[4] and row[4]["parsed"]["products"][0]["brand"] == "Biogesic"

    assert store.purge()["scan_log_ocr_text_cleared"] == 0          # too recent
    pg_execute("UPDATE scan_log SET created_at = now() - interval '31 days'")
    assert store.purge()["scan_log_ocr_text_cleared"] == 1
    assert pg_execute("SELECT ocr_text, status FROM scan_log") == [(None, "ok")]


def test_postgres_migrations_are_recorded_and_rls_is_on(pg_store):
    assert [r[0] for r in pg_execute("SELECT version FROM schema_version ORDER BY 1")] == \
        [v for v, _ in migrate.migration_files()]
    rls = dict(pg_execute("SELECT relname, relrowsecurity FROM pg_class "
                          "WHERE relname IN ('parses', 'answers', 'scan_log', 'rate_limits', 'schema_version')"))
    assert rls == {"parses": True, "answers": True, "scan_log": True, "rate_limits": True, "schema_version": True}


# --- failing open ---

def test_a_dead_database_fails_fast_then_is_skipped(monkeypatch):
    from app.store.postgres import PostgresStore
    monkeypatch.setattr(config, "DATABASE_SSLMODE", "disable")
    store = PostgresStore(DEAD_URL)
    try:
        started = time.monotonic()
        with pytest.raises(StoreUnavailable):
            store.get_answer("k")
        assert time.monotonic() - started < config.DB_CONNECT_TIMEOUT_S + 1.5
        started = time.monotonic()
        with pytest.raises(StoreSkipped):          # breaker open: no new connection attempt
            store.set_parse("k", {})
        assert time.monotonic() - started < 0.1
    finally:
        store.close()


def test_a_scan_still_answers_with_the_database_down(fake_llm, monkeypatch):
    from app.store.postgres import PostgresStore
    monkeypatch.setattr(config, "DATABASE_SSLMODE", "disable")
    store = PostgresStore(DEAD_URL)
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)
    try:
        for _ in range(2):
            r = identify_scan(ScanRequest(labels.BIOGESIC_TEXT), store)
            assert (r["status"], r["tier"], r["cache"]) == ("ok", "general", "miss")
    finally:
        store.close()


# --- setup ---

def test_make_store(monkeypatch):
    monkeypatch.setattr(config, "CACHE_BACKEND", "sqlite")
    assert isinstance(make_store(), SQLiteStore)
    monkeypatch.setattr(config, "CACHE_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        make_store()
    monkeypatch.setattr(config, "CACHE_BACKEND", "redis")
    with pytest.raises(RuntimeError, match="CACHE_BACKEND"):
        make_store()


def test_an_old_sqlite_cache_file_is_upgraded(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:   # the table as it was before M3
        conn.execute("CREATE TABLE answers (cache_key TEXT PRIMARY KEY, tier TEXT NOT NULL, product TEXT NOT NULL, "
                     "description TEXT NOT NULL, sources TEXT NOT NULL, cached_at TEXT NOT NULL)")
        conn.execute("INSERT INTO answers VALUES ('k', 'general', ?, 'Old answer.', '[]', '2026-10-01T00:00:00+00:00')",
                     (json.dumps(PRODUCT),))
    conn.close()
    a = SQLiteStore(path).get_answer("k")
    assert (a["description"], a["status"], a["expires_at"]) == ("Old answer.", "active", None)


def test_migration_files_are_numbered_without_gaps(tmp_path):
    assert [v for v, _ in migrate.migration_files()][:2] == [1, 2]
    (tmp_path / "0001_a.sql").write_text("")
    (tmp_path / "0003_c.sql").write_text("")
    with pytest.raises(ValueError, match="no gaps"):
        migrate.migration_files(tmp_path)
    (tmp_path / "0002-Bad Name.sql").write_text("")
    with pytest.raises(ValueError, match="named"):
        migrate.migration_files(tmp_path)
    assert migrate.pending({1}, [(1, tmp_path / "a"), (2, tmp_path / "b")]) == [(2, tmp_path / "b")]
