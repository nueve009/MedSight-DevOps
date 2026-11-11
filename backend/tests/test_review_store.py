"""The review console's SQL, as the medsight_review role on a real Postgres.
Needs MEDSIGHT_TEST_DATABASE_URL (a throwaway database, as for test_store.py). Fake rows only."""
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from app import config
from app.review_store import TABLES, Conflict, PostgresReviewStore, RowNotFound, RuleRefused
from tests.test_store import PG_SSLMODE, PG_URL, _reset_postgres, pg_execute

PRODUCTS = TABLES["products"]
INGREDIENTS = TABLES["ingredients"]
PASSAGES = TABLES["passages"]
USER = str(uuid.uuid4())
LOGIN = "medsight_review_test"
PRODUCT = {"product_id": "testflu-500-tab", "brand_name": "Testflu", "generic_names": "Fakeamol", "strengths": "500 mg",
           "dosage_form": "Tablet", "classification": "OTC", "fda_reg_no": "DR-XY00000", "reg_expiry": "2099-01-01",
           "portal_checked_on": "2026-11-10", "spoken_description": "Used to relieve test pain."}


def login_url() -> str:
    parts = urlsplit(PG_URL)
    return urlunsplit(parts._replace(netloc=f"{LOGIN}@{parts.hostname}:{parts.port}"))


@pytest.fixture
def store(monkeypatch):
    if not PG_URL:
        pytest.skip("MEDSIGHT_TEST_DATABASE_URL not set")
    monkeypatch.setattr(config, "DATABASE_SSLMODE", PG_SSLMODE)
    _reset_postgres()
    pg_execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_review_test') THEN "
               "CREATE ROLE medsight_review_test LOGIN IN ROLE medsight_review; END IF; END $$")
    pg_execute("INSERT INTO reviewers (user_id, name) VALUES (%s, 'Ben')", USER)
    pg_execute("INSERT INTO kb_ingredients (generic_name, entered_by) VALUES ('Fakeamol', 'Ana')")
    s = PostgresReviewStore(login_url())
    s.add_row(PRODUCTS, PRODUCT, "Ana")
    return s


def version(store) -> str:
    return store.get_row(PRODUCTS, "testflu-500-tab")["updated_at"]


def state():
    return pg_execute("SELECT approved, review_state, checked_by, description_checked_by FROM kb_products")[0]


def test_reviewer_lookup_only_finds_active_reviewers(store):
    assert store.reviewer_name(USER) == "Ben"
    pg_execute("UPDATE reviewers SET active = false")
    assert store.reviewer_name(USER) is None
    assert store.reviewer_name(str(uuid.uuid4())) is None


def test_queue_hides_your_own_rows_and_filters(store):
    assert sorted(r["key"] for r in store.queue(None, "", "Ben")) == ["Fakeamol", "testflu-500-tab"]
    assert store.queue(None, "", "ana") == []   # both rows were entered by Ana
    assert [r["key"] for r in store.queue(PRODUCTS, "flu", "Ben")] == ["testflu-500-tab"]
    assert store.queue(PRODUCTS, "nothing", "Ben") == []
    assert store.queue(PRODUCTS, "%", "Ben") == []   # a search is plain text, not a pattern


def test_queue_hides_the_last_editors_rows(store):
    store.update_row(PRODUCTS, "testflu-500-tab", {"aliases": "TF"}, "Cy")
    assert store.queue(PRODUCTS, "", "Cy") == []


def test_approve_writes_the_decision_and_the_approval_together(store):
    checklist = {"brand_matches_box": True}
    store.approve(PRODUCTS, "testflu-500-tab", "Ben", checklist, version(store))
    assert state() == (True, "approved", "Ben", "Ben")
    review = store.history(PRODUCTS, "testflu-500-tab")
    assert [(r["reviewer"], r["decision"], r["checklist"]) for r in review] == [("Ben", "approve", checklist)]


def test_a_refused_approval_leaves_no_decision_behind(store):
    with pytest.raises(RuleRefused, match="entered_by"):
        store.approve(PRODUCTS, "testflu-500-tab", "Ana", {}, version(store))
    assert store.history(PRODUCTS, "testflu-500-tab") == [] and state()[0] is False


def test_approving_a_row_that_changed_since_you_read_it_is_refused(store):
    read = version(store)
    store.update_row(PRODUCTS, "testflu-500-tab", {"aliases": "TF"}, "Cy")
    with pytest.raises(Conflict, match="changed"):
        store.approve(PRODUCTS, "testflu-500-tab", "Ben", {}, read)


def test_approving_twice_is_refused(store):
    store.approve(PRODUCTS, "testflu-500-tab", "Ben", {}, version(store))
    with pytest.raises(Conflict, match="already"):
        store.approve(PRODUCTS, "testflu-500-tab", "Cy", {}, version(store))


def test_a_missing_row_is_reported(store):
    with pytest.raises(RowNotFound):
        store.approve(PRODUCTS, "nothing", "Ben", {}, "x")
    with pytest.raises(RowNotFound):
        store.update_row(PRODUCTS, "nothing", {"aliases": "TF"}, "Ben")


def test_an_edit_un_approves_and_records_the_editor(store):
    store.approve(PRODUCTS, "testflu-500-tab", "Ben", {}, version(store))
    store.update_row(PRODUCTS, "testflu-500-tab", {"aliases": "TF"}, "Cy")
    assert state() == (False, "pending", "", "")
    assert store.get_row(PRODUCTS, "testflu-500-tab")["last_edited_by"] == "Cy"


def test_only_columns_a_reviewer_may_type_are_written(store):
    store.update_row(PRODUCTS, "testflu-500-tab", {"aliases": "TF", "entered_by": "Mallory", "approved": "true"}, "Cy")
    row = store.get_row(PRODUCTS, "testflu-500-tab")
    assert row["entered_by"] == "Ana" and row["approved"] is False


def test_request_changes_keeps_the_note_and_snapshot(store):
    store.request_changes(PRODUCTS, "testflu-500-tab", "Ben", "Wrong box.")
    assert state()[1] == "changes_requested"
    assert store.get_row(PRODUCTS, "testflu-500-tab")["review_note"] == "Wrong box."
    assert store.history(PRODUCTS, "testflu-500-tab")[0]["note"] == "Wrong box."
    assert store.queue(PRODUCTS, "", "Ben") == []


def test_request_changes_on_an_approved_row_is_refused(store):
    store.approve(PRODUCTS, "testflu-500-tab", "Ben", {}, version(store))
    with pytest.raises(Conflict):
        store.request_changes(PRODUCTS, "testflu-500-tab", "Cy", "No.")


def test_duplicate_registration_number_is_a_conflict(store):
    with pytest.raises(Conflict):
        store.add_row(PRODUCTS, {**PRODUCT, "product_id": "other-500-tab"}, "Ana")


def test_a_bad_date_is_refused_in_words(store):
    with pytest.raises(RuleRefused, match="format"):
        store.update_row(PRODUCTS, "testflu-500-tab", {"reg_expiry": "soon"}, "Cy")


def test_evidence_is_recorded(store):
    store.add_evidence(PRODUCTS, "testflu-500-tab", "box_photo", "products/a.jpg", "Ben")
    assert [(e["kind"], e["storage_path"]) for e in store.evidence(PRODUCTS, "testflu-500-tab")] == [
        ("box_photo", "products/a.jpg")]


def test_delete_goes_through_the_database_rule(store):
    with pytest.raises(RuleRefused, match="only the person"):
        store.delete_row(PRODUCTS, "testflu-500-tab", "Ben")
    store.delete_row(PRODUCTS, "testflu-500-tab", "Ana")
    assert store.get_row(PRODUCTS, "testflu-500-tab") is None


def test_fda_candidates_by_brand(store):
    pg_execute("INSERT INTO kb_fda_lookups (brand_query, brand_name, fda_reg_no, reg_expiry, export_only, pushed_by) "
               "VALUES ('Testflu', 'Testflu', 'DR-XY1', '2030-01-01', true, 'Ana')")
    found = store.fda_candidates("TESTFLU")
    assert [(c["fda_reg_no"], c["reg_expiry"], c["export_only"]) for c in found] == [("DR-XY1", "2030-01-01", True)]
    assert store.fda_candidate(found[0]["id"])["fda_reg_no"] == "DR-XY1"
    assert store.fda_candidates("Other") == [] and store.fda_candidate(999) is None


def test_stats_counts_each_state(store):
    assert store.stats()["products"] == {"pending": 1, "changes_requested": 0, "approved": 0}


def test_approval_context_for_a_product_and_a_passage(store):
    store.add_row(PASSAGES, {"passage_id": "p1", "product_id": "testflu-500-tab", "section": "indications",
                             "text": "For pain.", "source_type": "box", "source_ref": "photo"}, "Ana")
    context = store.approval_context(PRODUCTS, "testflu-500-tab")
    assert [p["passage_id"] for p in context["passages"]] == ["p1"]
    assert [i["generic_name"] for i in context["ingredients"]] == ["Fakeamol"]
    assert store.approval_context(PASSAGES, "p1")["product"]["product_id"] == "testflu-500-tab"
    assert store.get_row(INGREDIENTS, "Fakeamol")["spoken_description"] == ""


def test_an_approved_row_reaches_the_backend_role(store):
    # What Mon's "no deploy" depends on: the same row the backend's loader reads.
    store.approve(PRODUCTS, "testflu-500-tab", "Ben", {}, version(store))
    assert pg_execute("SELECT count(*) FROM kb_products WHERE approved")[0][0] == 1

# --- web answers (migration 0007) ---

WEB_PRODUCT_JSON = '{"brand": "Webflu", "generics": ["Newamol"], "strengths": ["500 mg"]}'
NEW_PRODUCT = {"product_id": "webflu-500-tab", "brand_name": "Webflu", "generic_names": "Newamol|Fakeamol",
               "strengths": "500 mg|10 mg", "dosage_form": "Tablet", "spoken_description": "Used to relieve test pain."}
NEW_PASSAGE = {"passage_id": "webflu-500-tab-ind-1", "product_id": "webflu-500-tab", "section": "indications",
               "text": "For test pain.", "source_type": "box", "source_ref": "box-1.jpg"}


def add_answer(key="k1", tier="web", status="pending_review", expires="now() + interval '30 days'"):
    pg_execute("INSERT INTO answers (cache_key, tier, product, description, source_urls, status, expires_at, "
               "page_text) VALUES (%s, %s, %s, 'Used to relieve test pain.', '[\"https://x.test\"]', %s, "
               f"{expires}, 'Page text.')",
               key, tier, WEB_PRODUCT_JSON, status)


def send(store, key="k1", actor="Ben"):
    return store.send_web_answer(key, actor, NEW_PRODUCT, NEW_PASSAGE, "Sent from a web answer: https://x.test")


def test_web_answers_lists_only_what_is_still_waiting(store):
    add_answer("waiting")
    add_answer("active", status="active")
    add_answer("rejected", status="rejected")
    add_answer("general", tier="general", status="active", expires="NULL")
    add_answer("expired", expires="now() - interval '1 day'")
    assert sorted(a["cache_key"] for a in store.web_answers()) == ["active", "waiting"]
    assert store.web_answer("waiting")["page_text"] == "Page text."
    assert store.web_answer("general") is None and store.web_answer("rejected") is None


def test_reject_stops_it_and_is_audited(store):
    add_answer()
    store.reject_web_answer("k1", "Ben")
    assert pg_execute("SELECT status FROM answers") == [("rejected",)]
    (entry,) = pg_execute("SELECT admin, action, cache_key, before->>'status', after->>'status' FROM audit_log")
    assert entry == ("Ben", "reject", "k1", "pending_review", "rejected")
    with pytest.raises(Conflict):
        store.reject_web_answer("k1", "Ben")


def test_reject_of_a_missing_or_general_answer_is_not_found(store):
    add_answer("general", tier="general", status="active", expires="NULL")
    for key in ("nope", "general"):
        with pytest.raises(RowNotFound):
            store.reject_web_answer(key, "Ben")


def test_send_creates_pending_rows_and_only_missing_ingredients(store):
    add_answer()
    assert send(store) == ["Newamol"]   # Fakeamol is already listed
    row = pg_execute("SELECT approved, review_state, entered_by, fda_reg_no, classification, checked_by, review_note "
                     "FROM kb_products WHERE product_id = 'webflu-500-tab'")[0]
    assert row == (False, "pending", "Ben", None, "", "", "Sent from a web answer: https://x.test")
    assert pg_execute("SELECT approved, entered_by, pnf_listed FROM kb_ingredients WHERE generic_name = 'Newamol'") == [
        (False, "Ben", "no")]
    assert pg_execute("SELECT approved, entered_by, source_type FROM kb_passages") == [(False, "Ben", "box")]
    assert pg_execute("SELECT cache_key, product_id, sent_by FROM kb_web_sends") == [("k1", "webflu-500-tab", "Ben")]
    assert pg_execute("SELECT admin, action, after->>'product_id' FROM audit_log") == [
        ("Ben", "send_to_kb", "webflu-500-tab")]


def test_a_sent_answer_leaves_the_tab_but_keeps_being_spoken(store):
    add_answer()
    send(store)
    assert store.web_answers() == []
    assert pg_execute("SELECT status FROM answers") == [("pending_review",)]


def test_send_is_all_or_nothing(store):
    add_answer()
    store.add_row(PRODUCTS, {**PRODUCT, "product_id": "webflu-500-tab", "fda_reg_no": "DR-XY00001"}, "Ana")
    with pytest.raises(Conflict):
        send(store)   # the product ID is taken, after the ingredient was inserted
    assert pg_execute("SELECT count(*) FROM kb_ingredients WHERE generic_name = 'Newamol'") == [(0,)]
    assert pg_execute("SELECT count(*) FROM kb_web_sends") == [(0,)]
    assert pg_execute("SELECT count(*) FROM audit_log") == [(0,)]


def test_an_answer_cannot_be_sent_twice(store):
    add_answer()
    send(store)
    with pytest.raises(Conflict):
        store.send_web_answer("k1", "Ben", {**NEW_PRODUCT, "product_id": "other"},
                              {**NEW_PASSAGE, "passage_id": "other-ind-1", "product_id": "other"}, "n")


def test_deleting_the_unapproved_product_returns_the_answer_to_the_tab(store):
    add_answer()
    send(store)
    store.delete_row(PRODUCTS, "webflu-500-tab", "Ben")
    assert [a["cache_key"] for a in store.web_answers()] == ["k1"]


def test_the_sender_cannot_approve_what_they_sent(store):
    add_answer()
    send(store)
    assert store.queue(PRODUCTS, "webflu", "Ben") == []
    assert [r["key"] for r in store.queue(PRODUCTS, "webflu", "Cy")] == ["webflu-500-tab"]
