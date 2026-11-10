"""The reviewed dataset in Supabase: approve-on-the-row rules (migration 0005),
importing drafts (kb_build --push), loading approved rows (app/kb_source.py)
and refreshing them in the running server. Fake rows only (fixtures/fake_kb.py).

The Postgres half needs MEDSIGHT_TEST_DATABASE_URL (a throwaway database, as
for test_store.py); the rest runs everywhere."""
import types

import pytest

from app import api, config, kb_source
from app.kb_validate import servable, validate_rows
from app.llm_parse import ParsedProduct
from app.retrieval import NONE, REVIEWED, KnowledgeBase
from scripts import kb_build
from tests.fixtures.fake_kb import review_rows, write_kb
from tests.test_store import PG_SSLMODE, PG_URL, _reset_postgres, pg_execute

BIOGESIC = ParsedProduct("Biogesic", ("Paracetamol",), ("500 mg",))


def table_rows(rows: dict) -> tuple[list, list, list]:
    """review_rows() as (where, row) lists, the way kb_source labels Supabase rows."""
    keys = {"products": "product_id", "ingredients": "generic_name", "passages": "passage_id"}
    return tuple([(f"kb_{name}:{r[keys[name]]}", r) for r in rows[name]]
                 for name in ("products", "ingredients", "passages"))


def with_entrants(rows: dict) -> dict:
    """Supabase needs entered_by on every row, names-only ingredients too."""
    for r in rows["ingredients"]:
        r["entered_by"] = r["entered_by"] or "Ana"
    return rows


# --- rules on loaded rows (no database) ---

def test_a_broken_approved_row_and_everything_that_needs_it_are_dropped():
    rows = review_rows()
    rows["ingredients"][0]["spoken_description"] = "TEST: take 2 tablets every 4 hours."   # Paracetamol: a dose
    data, dropped = servable(*table_rows(rows))
    assert any(e.startswith("kb_ingredients:Paracetamol:") and "dose" in e for e in dropped)
    left = {p["product_id"] for p in data["products"]}
    # every product with paracetamol goes, with its passage; the others stay
    assert left == {"dolfenal-500-tab", "buscopan-10-tab"}
    assert {p["product_id"] for p in data["passages"]} == left
    assert validate_rows(*table_rows(data), allow_empty=True).errors == []


def test_a_product_without_its_passage_is_dropped():
    rows = review_rows()
    rows["passages"] = [p for p in rows["passages"] if p["product_id"] != "biogesic-500-tab"]
    data, dropped = servable(*table_rows(rows))
    assert "biogesic-500-tab" not in {p["product_id"] for p in data["products"]}
    assert any("has no 'indications' passage" in e for e in dropped)


def test_valid_rows_are_all_served():
    data, dropped = servable(*table_rows(review_rows()))
    assert dropped == [] and len(data["products"]) == 7
    assert kb_source.dataset_version(data) == kb_source.dataset_version(servable(*table_rows(review_rows()))[0])


def test_an_empty_dataset_has_no_version():
    assert kb_source.dataset_version({"products": [], "ingredients": [], "passages": []}) is None


def test_push_drafts_may_leave_the_checker_columns_empty(tmp_path):
    rows = with_entrants(review_rows())
    for table in rows.values():
        for r in table:
            r["checked_by"] = ""
            if "description_checked_by" in r:
                r["description_checked_by"] = ""
    kb_dir = write_kb(tmp_path / "kb", rows)
    assert kb_build.validate(kb_dir, for_review=True)[0].errors == []
    assert kb_build.validate(kb_dir)[0].errors != []          # not as the reviewed CSVs


def test_push_drafts_need_who_entered_every_ingredient(tmp_path):
    v, _ = kb_build.validate(write_kb(tmp_path / "kb", review_rows()), for_review=True)
    assert any("'entered_by' is empty; Supabase needs" in e for e in v.errors)


def test_a_passage_of_an_unapproved_product_is_skipped_quietly(monkeypatch):
    rows = review_rows()
    rows["products"] = [p for p in rows["products"] if p["product_id"] != "biogesic-500-tab"]   # edited, unapproved
    products, ingredients, passages = table_rows(rows)
    monkeypatch.setattr(kb_source, "fetch_approved", lambda conninfo: {
        "products": products, "ingredients": ingredients, "passages": passages})
    data, version, dropped = kb_source.servable_from_postgres("postgresql://unused")
    assert dropped == [] and len(data["products"]) == 6 and version
    assert "biogesic-500-tab-ind-1" not in {p["passage_id"] for p in data["passages"]}


# --- the refresher (no database) ---

def kb_with_version(version):
    return KnowledgeBase(version=version)


def test_refresher_swaps_in_a_new_version_and_keeps_it_when_a_load_fails(caplog):
    state = types.SimpleNamespace(kb=KnowledgeBase())
    replies = [(kb_with_version("v1"), []), (kb_with_version("v1"), []), RuntimeError("down"),
               (kb_with_version("v2"), ["kb_products:x: broken"])]

    def loader():
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    r = kb_source.KBRefresher(state, loader, interval_s=60)
    assert r.load_once() and state.kb.version == "v1"
    first = state.kb
    assert r.load_once() and state.kb is first          # same version: not replaced
    assert not r.load_once() and state.kb is first      # outage: keep what we have
    assert r.load_once() and state.kb.version == "v2"
    messages = [rec.getMessage() for rec in caplog.records]
    assert "kb load failed; keeping the current dataset" in messages
    assert "kb row not served" in messages


def test_start_kb_needs_a_database_url(monkeypatch):
    monkeypatch.setattr(config, "KB_BACKEND", "postgres")
    monkeypatch.setattr(config, "KB_DATABASE_URL", "")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="KB_BACKEND=postgres needs"):
        api.start_kb(types.SimpleNamespace())


def test_start_kb_runs_on_an_empty_dataset_while_supabase_is_down(monkeypatch):
    monkeypatch.setattr(config, "KB_BACKEND", "postgres")
    monkeypatch.setattr(config, "KB_DATABASE_URL", "postgresql://x")

    def down(conninfo):
        raise OSError("unreachable")

    monkeypatch.setattr(api, "load_from_postgres", down)
    state = types.SimpleNamespace()
    refresher = api.start_kb(state)
    try:
        assert state.kb.products == [] and state.kb.match(BIOGESIC).kind == NONE
    finally:
        refresher.stop()


def test_start_kb_rejects_an_unknown_backend(monkeypatch):
    monkeypatch.setattr(config, "KB_BACKEND", "csv")
    with pytest.raises(RuntimeError, match="KB_BACKEND must be"):
        api.start_kb(types.SimpleNamespace())


# --- Supabase (Postgres) ---

@pytest.fixture
def pg(monkeypatch, tmp_path):
    if not PG_URL:
        pytest.skip("MEDSIGHT_TEST_DATABASE_URL not set")
    monkeypatch.setattr(config, "DATABASE_SSLMODE", PG_SSLMODE)
    # The backend role, so 0005 creates its read-only policies (roles are per server, not per database).
    pg_execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_backend') "
               "THEN CREATE ROLE medsight_backend; END IF; END $$")
    _reset_postgres()
    return PG_URL


def push_rows(tmp_path, rows=None) -> dict:
    kb_dir = write_kb(tmp_path / "kb", with_entrants(review_rows() if rows is None else rows))
    v, data = kb_build.validate(kb_dir, for_review=True)
    assert v.errors == []
    return kb_build.push(data, PG_URL)


def approve_all(checker="Ben") -> None:
    for table in ("kb_ingredients", "kb_products", "kb_passages"):
        extra = ", description_checked_by = 'Cy'" if table == "kb_products" else ""
        pg_execute(f"UPDATE {table} SET checked_by = %s{extra}", checker)
        pg_execute(f"UPDATE {table} SET approved = true")


def load(url=None) -> KnowledgeBase:
    kb, dropped = kb_source.load_from_postgres(url or PG_URL)
    assert dropped == []
    return kb


def test_pushed_rows_wait_for_approval(pg, tmp_path):
    report = push_rows(tmp_path)
    assert report["kb_products"]["added"] == 7
    assert pg_execute("SELECT count(*) FROM kb_products WHERE approved") == [(0,)]
    kb = load()
    assert kb.products == [] and kb.version is None and kb.match(BIOGESIC).kind == NONE


def test_approved_rows_are_served(pg, tmp_path):
    push_rows(tmp_path)
    approve_all()
    kb = load()
    assert len(kb.products) == 7 and kb.version
    m = kb.match(BIOGESIC)
    assert m.kind == REVIEWED and m.description == "TEST: relieves mild pain and fever."


def test_the_same_person_cannot_approve_their_own_row(pg, tmp_path):
    push_rows(tmp_path)
    pg_execute("UPDATE kb_ingredients SET checked_by = 'ana' WHERE generic_name = 'Paracetamol'")
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="different person"):
        pg_execute("UPDATE kb_ingredients SET approved = true WHERE generic_name = 'Paracetamol'")


def test_a_row_cannot_be_added_approved(pg):
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="second teammate approves"):
        pg_execute("INSERT INTO kb_ingredients (generic_name, entered_by, checked_by, approved) "
                   "VALUES ('Fakeamol', 'Ana', 'Ben', true)")


def test_a_dose_in_the_description_blocks_approval(pg, tmp_path):
    push_rows(tmp_path)
    approve_all()
    pg_execute("UPDATE kb_products SET spoken_description = 'Take 2 tablets every 4 hours.' "
               "WHERE product_id = 'biogesic-500-tab'")
    pg_execute("UPDATE kb_products SET checked_by = 'Ben', description_checked_by = 'Cy' "
               "WHERE product_id = 'biogesic-500-tab'")
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="dose or schedule"):
        pg_execute("UPDATE kb_products SET approved = true WHERE product_id = 'biogesic-500-tab'")


def test_editing_an_approved_row_takes_it_off_the_air(pg, tmp_path):
    push_rows(tmp_path)
    approve_all()
    pg_execute("UPDATE kb_products SET strengths = '650 mg' WHERE product_id = 'biogesic-500-tab'")
    assert pg_execute("SELECT approved, checked_by, description_checked_by FROM kb_products "
                      "WHERE product_id = 'biogesic-500-tab'") == [(False, "", "")]
    kb = load()
    assert "biogesic-500-tab" not in {p.product_id for p in kb.products}
    assert len(kb.products) == 6


def test_a_review_note_does_not_unapprove(pg, tmp_path):
    push_rows(tmp_path)
    approve_all()
    pg_execute("UPDATE kb_products SET review_note = 'checked the box photo' WHERE product_id = 'biogesic-500-tab'")
    assert pg_execute("SELECT approved FROM kb_products WHERE product_id = 'biogesic-500-tab'") == [(True,)]


def test_pushing_again_keeps_unchanged_rows_approved(pg, tmp_path):
    push_rows(tmp_path)
    approve_all()
    rows = review_rows()
    rows["products"][3]["spoken_description"] = "TEST: a changed description."   # tempra-325-tab
    report = push_rows(tmp_path, rows)
    assert (report["kb_products"]["unchanged"], report["kb_products"]["updated"]) == (6, 1)
    approved = dict(pg_execute("SELECT product_id, approved FROM kb_products"))
    assert approved["tempra-325-tab"] is False and approved["biogesic-500-tab"] is True


def test_every_change_is_logged(pg, tmp_path):
    push_rows(tmp_path)
    approve_all()
    actions = pg_execute("SELECT action, count(*) FROM kb_review_log WHERE table_name = 'kb_products' GROUP BY 1")
    assert dict(actions) == {"insert": 7, "update": 14}


def test_the_backend_role_reads_only_approved_rows_and_changes_nothing(pg, tmp_path):
    push_rows(tmp_path)
    pg_execute("UPDATE kb_ingredients SET checked_by = 'Ben'")
    pg_execute("UPDATE kb_ingredients SET approved = true WHERE generic_name = 'Paracetamol'")
    import psycopg
    with psycopg.connect(PG_URL, sslmode=PG_SSLMODE, autocommit=True) as conn:
        conn.execute("SET ROLE medsight_backend")
        assert conn.execute("SELECT generic_name FROM kb_ingredients").fetchall() == [("Paracetamol",)]
        assert conn.execute("SELECT count(*) FROM kb_products").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE kb_ingredients SET synonyms = 'x'")


def test_pull_builds_the_local_kb_sqlite(pg, tmp_path, monkeypatch):
    push_rows(tmp_path)
    approve_all()
    monkeypatch.setattr(config, "KB_DATABASE_URL", PG_URL)
    assert kb_build.pull(tmp_path / "out") == 0
    kb = KnowledgeBase.load(config.KB_DB_PATH)
    assert len(kb.products) == 7 and kb.match(BIOGESIC).kind == REVIEWED
    v, _ = kb_build.validate(tmp_path / "out")
    assert v.errors == []
