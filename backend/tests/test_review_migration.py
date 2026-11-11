"""Migration 0006 on a real Postgres: approving, the review role and append-only history.
Needs MEDSIGHT_TEST_DATABASE_URL (a throwaway database, as for test_store.py). Fake rows only."""
import psycopg
import pytest
from psycopg import sql

from tests.test_store import PG_SSLMODE, PG_URL, _reset_postgres, pg_execute

LOGIN = "medsight_review_test"


@pytest.fixture
def db():
    if not PG_URL:
        pytest.skip("MEDSIGHT_TEST_DATABASE_URL not set")
    _reset_postgres()
    pg_execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsight_review_test') THEN "
               "CREATE ROLE medsight_review_test LOGIN IN ROLE medsight_review; END IF; END $$")
    pg_execute("INSERT INTO kb_ingredients (generic_name, entered_by) VALUES ('Fakeamol', 'Ana')")
    pg_execute("INSERT INTO kb_products (product_id, brand_name, generic_names, strengths, dosage_form, "
               "classification, fda_reg_no, reg_expiry, portal_checked_on, entered_by, spoken_description) VALUES "
               "('testflu-500-tab', 'Testflu', 'Fakeamol', '500 mg', 'Tablet', 'OTC', 'DR-XY00000', "
               "current_date + 365, current_date, 'Ana', 'Used to relieve test pain.')")


def as_review(statement: str, *args):
    with psycopg.connect(PG_URL, sslmode=PG_SSLMODE, autocommit=True) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(LOGIN)))
        cur = conn.execute(statement, args)
        return cur.fetchall() if cur.description else None


APPROVE = ("UPDATE kb_products SET checked_by = %s, description_checked_by = %s, approved = true "
           "WHERE product_id = 'testflu-500-tab'")


def state():
    return pg_execute("SELECT approved, review_state, checked_by FROM kb_products")[0]


def test_review_role_can_approve_a_row(db):
    as_review(APPROVE, "Ben", "Ben")
    assert state() == (True, "approved", "Ben")


def test_approver_must_differ_from_entrant_and_last_editor(db):
    with pytest.raises(psycopg.errors.RaiseException, match="entered_by"):
        as_review(APPROVE, "Ana", "Ana")
    as_review("UPDATE kb_products SET aliases = 'TF', last_edited_by = 'Cy'")
    with pytest.raises(psycopg.errors.RaiseException, match="last editor"):
        as_review(APPROVE, "Cy", "Cy")


def test_an_edit_sends_an_approved_row_back_to_the_queue(db):
    as_review(APPROVE, "Ben", "Ben")
    as_review("UPDATE kb_products SET aliases = 'TF', last_edited_by = 'Cy'")
    assert state() == (False, "pending", "")


def test_review_state_changes_alone_are_not_edits(db):
    as_review(APPROVE, "Ben", "Ben")
    assert state()[0] is True
    assert pg_execute("SELECT count(*) FROM kb_review_log WHERE action = 'update'")[0][0] == 1


def test_changes_requested_goes_back_to_pending_on_an_edit(db):
    as_review("UPDATE kb_products SET review_state = 'changes_requested'")
    assert state()[1] == "changes_requested"
    as_review("UPDATE kb_products SET aliases = 'TF', last_edited_by = 'Ana'")
    assert state()[1] == "pending"


def test_approved_state_cannot_be_set_without_approving(db):
    as_review("UPDATE kb_products SET review_state = 'approved'")
    assert state()[:2] == (False, "pending")


def test_owner_can_approve_only_while_the_setting_allows_it(db):
    owner_approve = "UPDATE kb_products SET checked_by = 'Ben', description_checked_by = 'Ben', approved = true"
    pg_execute("UPDATE kb_settings SET allow_table_editor_approval = false")
    with pytest.raises(psycopg.errors.RaiseException, match="review console"):
        pg_execute(owner_approve)
    pg_execute("UPDATE kb_settings SET allow_table_editor_approval = true")
    pg_execute(owner_approve)
    assert state()[0] is True


def test_review_history_is_append_only(db):
    add = ("INSERT INTO kb_reviews (table_name, row_key, reviewer, decision, row_snapshot) "
           "VALUES ('kb_products', 'testflu-500-tab', 'Ben', 'approve', '{}')")
    as_review(add)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review("DELETE FROM kb_reviews")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review("UPDATE kb_reviews SET reviewer = 'X'")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):   # not even the owner
        pg_execute("DELETE FROM kb_reviews")


@pytest.mark.parametrize("table", ["parses", "scan_log", "rate_limits", "audit_log", "kb_settings"])
def test_review_role_has_no_access_to_other_tables(db, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)).as_string())


def test_review_role_cannot_delete_directly_or_edit_reviewers(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review("DELETE FROM kb_products")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review("INSERT INTO reviewers (user_id, name) VALUES (gen_random_uuid(), 'Mallory')")


def test_only_the_entrant_deletes_and_only_a_never_approved_row(db):
    delete = "SELECT kb_delete_unapproved('kb_products', 'testflu-500-tab', %s)"
    with pytest.raises(psycopg.errors.RaiseException, match="only the person"):
        as_review(delete, "Ben")
    as_review(APPROVE, "Ben", "Ben")
    as_review("UPDATE kb_products SET approved = false, checked_by = ''")   # un-approved again
    with pytest.raises(psycopg.errors.RaiseException, match="approved before"):
        as_review(delete, "Ana")


def test_the_entrant_can_delete_a_never_approved_row(db):
    as_review("SELECT kb_delete_unapproved('kb_products', 'testflu-500-tab', %s)", "Ana")
    assert pg_execute("SELECT count(*) FROM kb_products")[0][0] == 0


def test_existing_approved_rows_get_the_approved_state(db):
    # 0005-era data: approved before review_state existed. The backfill must not count as an edit.
    pg_execute("UPDATE kb_products SET checked_by = 'Ben', description_checked_by = 'Ben', approved = true")
    pg_execute("UPDATE kb_products SET review_state = 'pending'")
    pg_execute("UPDATE kb_products SET review_state = 'approved' WHERE approved")
    assert state() == (True, "approved", "Ben")

# --- 0007: web answers ---

def add_web(key="k1", tier="web"):
    pg_execute("INSERT INTO answers (cache_key, tier, product, description, status) "
               "VALUES (%s, %s, '{}', 'Test.', 'pending_review')", key, tier)


def test_review_role_sees_web_answers_only(db):
    add_web("web")
    add_web("general", "general")
    assert as_review("SELECT cache_key FROM answers") == [("web",)]


def test_review_role_can_only_reject_an_answer(db):
    add_web()
    as_review("UPDATE answers SET status = 'rejected'")
    assert pg_execute("SELECT status FROM answers") == [("rejected",)]
    pg_execute("UPDATE answers SET status = 'pending_review'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review("UPDATE answers SET status = 'active'")   # a policy: only 'rejected' is allowed
    for statement in ("UPDATE answers SET description = 'Changed.'", "UPDATE answers SET tier = 'general'",
                      "DELETE FROM answers", "INSERT INTO answers (cache_key, tier, product, description) "
                                             "VALUES ('x', 'web', '{}', 'New.')"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_review(statement)


def test_review_role_cannot_touch_a_general_answer(db):
    add_web("general", "general")
    as_review("UPDATE answers SET status = 'rejected'")   # invisible, so nothing to update
    assert pg_execute("SELECT status FROM answers") == [("pending_review",)]


def test_review_role_can_only_add_its_own_audit_actions(db):
    as_review("INSERT INTO audit_log (admin, action, cache_key) VALUES ('Ben', 'reject', 'k')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_review("INSERT INTO audit_log (admin, action, cache_key) VALUES ('Ben', 'delete', 'k')")
    for statement in ("SELECT * FROM audit_log", "UPDATE audit_log SET admin = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_review(statement)


def test_a_link_can_be_added_but_not_changed(db):
    as_review("INSERT INTO kb_web_sends (cache_key, product_id, sent_by) VALUES ('k', 'testflu-500-tab', 'Ben')")
    for statement in ("UPDATE kb_web_sends SET sent_by = 'x'", "DELETE FROM kb_web_sends"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_review(statement)


def test_answers_already_cached_get_an_empty_page_text(db):
    add_web()
    assert pg_execute("SELECT page_text FROM answers") == [("",)]
