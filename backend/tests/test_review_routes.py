"""The review console's routes, on a fake store. The SQL itself is in test_review_store.py."""
import time
import uuid
from datetime import date, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, review
from app.api import app as api_app
from app.review_store import TABLES, Conflict, RuleRefused

SECRET = "test-secret-that-is-long-enough-for-hs256-signing"
URL = "https://project.test"
USERS = {"Ana": str(uuid.uuid4()), "Ben": str(uuid.uuid4()), "Cy": str(uuid.uuid4())}
FUTURE = (date.today() + timedelta(days=400)).isoformat()
PRODUCT_CHECKLIST = {item: True for item in review.CHECKLISTS["products"]}


def product_row(**changes) -> dict:
    return {"product_id": "testflu-500-tab", "brand_name": "Testflu", "aliases": "", "generic_names": "Fakeamol",
            "strengths": "500 mg", "dosage_form": "Tablet", "classification": "OTC", "fda_reg_no": "DR-XY00000",
            "reg_expiry": FUTURE, "manufacturer": "Test Co", "portal_checked_on": "2026-11-10", "entered_by": "Ana",
            "checked_by": "", "spoken_description": "Used to relieve test pain.", "description_checked_by": "",
            "approved": False, "review_state": "pending", "last_edited_by": "", "review_note": "",
            "updated_at": "2026-11-10T00:00:00+00:00", **changes}


def ingredient_row() -> dict:
    return {"generic_name": "Fakeamol", "synonyms": "", "pnf_listed": "no", "pnf_forms": "", "pnf_page": "",
            "spoken_description": "", "source_url": "", "entered_by": "Ana", "checked_by": "", "approved": True,
            "review_state": "approved", "last_edited_by": "", "review_note": "", "updated_at": "x"}


def passage_row() -> dict:
    return {"passage_id": "testflu-500-tab-ind-1", "product_id": "testflu-500-tab", "section": "indications",
            "text": "For test pain.", "source_type": "box", "source_ref": "photo-1.jpg", "entered_by": "Ana",
            "checked_by": "", "approved": False, "review_state": "pending", "last_edited_by": "", "review_note": "",
            "updated_at": "x"}


def candidate(**changes) -> dict:
    return {"id": 1, "brand_query": "Testflu", "brand_name": "Testflu", "generic_name": "Fakeamol",
            "strength": "500 mg", "dosage_form": "Tablet", "fda_reg_no": "DR-XY99999", "reg_expiry": FUTURE,
            "classification": "OTC", "classification_raw": "OTC", "manufacturer": "Portal Co",
            "portal_checked_on": "2026-11-10", "note": "", "export_only": False, **changes}


class FakeStore:
    def __init__(self):
        self.rows = {"products": {"testflu-500-tab": product_row()}, "ingredients": {"Fakeamol": ingredient_row()},
                     "passages": {"testflu-500-tab-ind-1": passage_row()}}
        self.candidates = [candidate()]
        self.calls = []
        self.queue_calls = []

    def reviewer_name(self, user_id):
        return next((name for name, uid in USERS.items() if uid == user_id), None)

    def queue(self, table, q, actor):
        self.queue_calls.append((table.api_name if table else None, q, actor))
        return []

    def mine(self, actor):
        return []

    def stats(self):
        return {"products": {"pending": 1}}

    def get_row(self, table, key):
        return self.rows[table.api_name].get(key)

    def history(self, table, key):
        return []

    def evidence(self, table, key):
        return [{"id": 1, "kind": "box_photo", "storage_path": "products/a.jpg", "uploaded_by": "Ana"}]

    def add_evidence(self, table, key, kind, path, actor):
        self.calls.append(("evidence", key, kind, path, actor))

    def add_row(self, table, values, actor):
        self.calls.append(("add", table.api_name, values, actor))

    def update_row(self, table, key, changes, actor):
        self.calls.append(("update", key, changes, actor))

    def approve(self, table, key, actor, checklist, version):
        self.calls.append(("approve", key, actor, checklist, version))

    def request_changes(self, table, key, actor, note):
        self.calls.append(("request_changes", key, actor, note))

    def delete_row(self, table, key, actor):
        self.calls.append(("delete", key, actor))

    def approval_context(self, table, key):
        return {"ingredients": [ingredient_row()], "passages": [passage_row()], "product": product_row()}

    def fda_candidates(self, brand):
        return self.candidates

    def fda_candidate(self, candidate_id):
        return next((c for c in self.candidates if c["id"] == candidate_id), None)


class FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload(self, token, path, data, content_type):
        self.uploads.append((token, path, data, content_type))

    def signed_url(self, token, path, seconds):
        return f"https://storage.test/{path}?seconds={seconds}"


def auth(name: str = "Ben", **changes) -> dict:
    claims = {"sub": USERS[name], "aud": "authenticated", "exp": int(time.time()) + 600, "iss": f"{URL}/auth/v1",
              "aal": "aal2", "is_anonymous": False, **changes}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def client(monkeypatch, store, storage):
    monkeypatch.setattr(config, "ADMIN_ENABLED", True)
    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", SECRET)
    app = FastAPI()
    app.include_router(review.router)
    app.state.review_store = store
    app.state.review_storage = storage
    return TestClient(app)


PATH = "/admin/kb/products/testflu-500-tab"
APPROVE = {"checklist": PRODUCT_CHECKLIST, "version": "2026-11-10T00:00:00+00:00"}


# --- access ---

@pytest.mark.parametrize("method, path", [
    ("get", "/admin/kb/queue"), ("get", "/admin/kb/mine"), ("get", "/admin/kb/meta"), ("get", "/admin/review/stats"),
    ("get", PATH), ("get", f"{PATH}/fda-candidates"), ("delete", PATH),
])
def test_every_route_needs_a_reviewer(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_routes_are_hidden_when_admin_is_off(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ENABLED", False)
    assert client.get("/admin/kb/queue", headers=auth()).status_code == 404


def test_the_main_api_gates_the_console_too(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ENABLED", False)
    with TestClient(api_app) as c:
        assert c.get("/admin/kb/queue").status_code == 404


def test_first_factor_only_is_refused_and_says_why(client):
    r = client.get("/admin/kb/queue", headers=auth(aal="aal1"))
    assert r.status_code == 403 and "two-factor" in r.json()["detail"]


def test_a_refusal_is_logged(client, caplog):
    client.get("/admin/kb/queue", headers=auth(aal="aal1"))
    assert any(r.message == "review console refused" and r.fields["reason"] == "two_factor_required"
               for r in caplog.records if hasattr(r, "fields"))


@pytest.mark.parametrize("path", ["/admin/kb/users/x", "/admin/kb/answers/x", "/admin/kb/kb_products/x"])
def test_only_listed_tables_are_reachable(client, path):
    assert client.get(path, headers=auth()).status_code == 404


def test_queue_passes_the_signed_in_name_and_filters(client, store):
    assert client.get("/admin/kb/queue?table=products&q=flu&reviewer=Ana", headers=auth("Ben")).status_code == 200
    assert store.queue_calls == [("products", "flu", "Ben")]


def test_unknown_queue_table_is_refused(client):
    assert client.get("/admin/kb/queue?table=reviewers", headers=auth()).status_code == 404


# --- reading a row ---

def test_row_page_has_the_spoken_preview_links_and_checklist(client):
    body = client.get(PATH, headers=auth()).json()
    assert body["spoken_preview"] == ("Testflu, Fakeamol 500 milligrams. Our team has reviewed this information. "
                                      "Used to relieve test pain. Please check the label or ask a pharmacist before "
                                      "taking it.")
    assert body["evidence"][0]["url"].endswith("seconds=300")
    assert [c["key"] for c in body["checklist"]] == list(review.CHECKLISTS["products"])
    assert body["own_row"] is False


def test_preview_says_prescription_for_an_rx_product(client, store):
    store.rows["products"]["testflu-500-tab"]["classification"] = "Rx"
    assert "This is a prescription medicine." in client.get(PATH, headers=auth()).json()["spoken_preview"]


def test_missing_row_is_404(client):
    assert client.get("/admin/kb/products/nothing", headers=auth()).status_code == 404


# --- approving ---

def test_approve_records_the_signed_in_reviewer(client, store):
    assert client.post(f"{PATH}/approve", json=APPROVE, headers=auth("Ben")).status_code == 200
    assert store.calls == [("approve", "testflu-500-tab", "Ben", PRODUCT_CHECKLIST, APPROVE["version"])]


def test_a_name_in_the_body_is_refused(client):
    assert client.post(f"{PATH}/approve", json={**APPROVE, "reviewer": "Ana"}, headers=auth("Ben")).status_code == 422


@pytest.mark.parametrize("who", ["Ana", "ANA "])
def test_the_person_who_entered_a_row_cannot_approve_it(client, store, who):
    store.rows["products"]["testflu-500-tab"]["entered_by"] = who
    assert client.post(f"{PATH}/approve", json=APPROVE, headers=auth("Ana")).status_code == 403
    assert store.calls == []


def test_the_last_editor_cannot_approve_it(client, store):
    store.rows["products"]["testflu-500-tab"]["last_edited_by"] = "Ben"
    assert client.post(f"{PATH}/approve", json=APPROVE, headers=auth("Ben")).status_code == 403


@pytest.mark.parametrize("item", list(review.CHECKLISTS["products"]))
def test_every_checklist_item_must_be_ticked(client, store, item):
    for checklist in ({**PRODUCT_CHECKLIST, item: False}, {k: v for k, v in PRODUCT_CHECKLIST.items() if k != item}):
        r = client.post(f"{PATH}/approve", json={**APPROVE, "checklist": checklist}, headers=auth())
        assert r.status_code == 422 and item in r.json()["detail"]
    assert store.calls == []


@pytest.mark.parametrize("change, message", [
    ({"spoken_description": "Take 2 tablets every 4 hours."}, "dose"),
    ({"reg_expiry": "2020-01-01"}, "expired"),
    ({"strengths": "500 mg|10 mg"}, "strength"),
    ({"classification": ""}, "classification"),
    ({"fda_reg_no": ""}, "fda_reg_no"),
])
def test_a_row_that_breaks_a_rule_is_not_approved(client, store, change, message):
    store.rows["products"]["testflu-500-tab"].update(change)
    r = client.post(f"{PATH}/approve", json=APPROVE, headers=auth())
    assert r.status_code == 422 and message in " ".join(r.json()["detail"])
    assert store.calls == []


def test_a_product_with_no_indications_passage_is_not_approved(client, store):
    store.approval_context = lambda table, key: {"ingredients": [ingredient_row()], "passages": [], "product": None}
    r = client.post(f"{PATH}/approve", json=APPROVE, headers=auth())
    assert r.status_code == 422 and "indications" in " ".join(r.json()["detail"])


def test_a_stale_approval_is_a_conflict(client, store):
    def stale(*args):
        raise Conflict("This row changed while you were reading it. Open it again.")

    store.approve = stale
    r = client.post(f"{PATH}/approve", json=APPROVE, headers=auth())
    assert r.status_code == 409 and "changed" in r.json()["detail"]


def test_a_database_refusal_reaches_the_reviewer(client, store):
    def refused(*args):
        raise RuleRefused("approve this row in the review console")

    store.approve = refused
    r = client.post(f"{PATH}/approve", json=APPROVE, headers=auth())
    assert r.status_code == 422 and "review console" in r.json()["detail"]


def test_passage_approval_uses_its_own_checklist(client, store):
    body = {"checklist": {"text_matches_source": True}, "version": "x"}
    assert client.post("/admin/kb/passages/testflu-500-tab-ind-1/approve", json=body, headers=auth()).status_code == 200


# --- request changes ---

@pytest.mark.parametrize("note", ["", "   "])
def test_request_changes_needs_a_note(client, store, note):
    assert client.post(f"{PATH}/request-changes", json={"note": note}, headers=auth()).status_code == 422
    assert store.calls == []


def test_request_changes_records_the_note(client, store):
    assert client.post(f"{PATH}/request-changes", json={"note": " Wrong box. "}, headers=auth("Cy")).status_code == 200
    assert store.calls == [("request_changes", "testflu-500-tab", "Cy", "Wrong box.")]


# --- adding and editing ---

def test_add_uses_the_signed_in_name(client, store):
    body = {"values": {"product_id": "new-10-tab", "brand_name": "New", "generic_names": "Fakeamol",
                       "strengths": "10 mg"}}
    assert client.post("/admin/kb/products", json=body, headers=auth("Cy")).status_code == 201
    kind, table, row, actor = store.calls[0]
    assert (kind, table, actor, row["entered_by"]) == ("add", "products", "Cy", "Cy")


@pytest.mark.parametrize("field", ["entered_by", "checked_by", "approved", "review_state", "last_edited_by"])
def test_who_and_approval_columns_cannot_be_typed(client, store, field):
    body = {"values": {"product_id": "new-10-tab", "brand_name": "New", field: "Mallory"}}
    assert client.post("/admin/kb/products", json=body, headers=auth()).status_code == 422
    assert client.patch(PATH, json={"values": {field: "Mallory"}}, headers=auth()).status_code == 422
    assert store.calls == []


def test_a_draft_may_wait_for_its_fda_values_but_not_have_bad_ones(client):
    base = {"product_id": "new-10-tab", "brand_name": "New", "generic_names": "Fakeamol", "strengths": "10 mg"}
    assert client.post("/admin/kb/products", json={"values": base}, headers=auth()).status_code == 201
    bad = client.post("/admin/kb/products", json={"values": {**base, "reg_expiry": "soon"}}, headers=auth())
    assert bad.status_code == 422 and "reg_expiry" in bad.json()["detail"][0]
    dosing = client.post("/admin/kb/products", json={"values": {
        **base, "spoken_description": "Take 2 tablets daily dose."}}, headers=auth())
    assert dosing.status_code == 422


def test_add_refuses_a_key_with_a_slash(client):
    body = {"values": {"generic_name": "a/b"}}
    assert client.post("/admin/kb/ingredients", json=body, headers=auth()).status_code == 422


def test_edit_records_the_editor(client, store):
    assert client.patch(PATH, json={"values": {"aliases": " TF "}}, headers=auth("Cy")).status_code == 200
    assert store.calls == [("update", "testflu-500-tab", {"aliases": "TF"}, "Cy")]


def test_the_key_cannot_be_edited(client):
    assert client.patch(PATH, json={"values": {"product_id": "other"}}, headers=auth()).status_code == 422


def test_delete_passes_the_signed_in_name(client, store):
    assert client.delete(PATH, headers=auth("Ana")).status_code == 200
    assert store.calls == [("delete", "testflu-500-tab", "Ana")]


# --- evidence ---

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def upload(client, data, name="photo.jpg", kind="box_photo", content_type="image/jpeg"):
    return client.post(f"{PATH}/evidence", data={"kind": kind}, files={"file": (name, data, content_type)},
                       headers=auth("Ana"))


@pytest.mark.parametrize("data, content_type, extension", [(JPEG, "image/jpeg", "jpg"), (PNG, "image/png", "png")])
def test_real_images_are_stored_under_a_random_name(client, store, storage, data, content_type, extension):
    r = upload(client, data, name="../../evil name.exe")
    assert r.status_code == 201
    token, path, stored, sent_type = storage.uploads[0]
    assert stored == data and sent_type == content_type and token
    assert path.endswith(f".{extension}") and "evil" not in path and ".." not in path
    assert store.calls[0][:3] == ("evidence", "testflu-500-tab", "box_photo")


@pytest.mark.parametrize("data", [b"<svg onload=alert(1)>", b"%PDF-1.7", b"", b"MZ\x90\x00"])
def test_other_files_are_refused_whatever_they_claim_to_be(client, storage, data):
    assert upload(client, data).status_code == 415
    assert storage.uploads == []


def test_over_5_mb_is_refused(client, storage):
    assert upload(client, JPEG + b"0" * config.MAX_EVIDENCE_BYTES).status_code == 413
    assert storage.uploads == []


def test_unknown_evidence_kind_is_refused(client):
    assert upload(client, JPEG, kind="selfie").status_code == 422


# --- FDA candidates ---

def flags(*candidates, today=date(2026, 11, 10)):
    return [c["flags"] for c in review.candidate_flags(list(candidates), today)]


def test_no_warnings_on_a_clean_candidate():
    assert flags(candidate(reg_expiry="2030-01-01")) == [[]]


def test_otc_and_rx_on_the_same_product_flags_both():
    result = flags(candidate(strength="500 mg", reg_expiry="2030-01-01"),
                   candidate(strength="500mg", classification="Rx", reg_expiry="2030-01-01"))
    assert result == [["otc_and_rx_same_product"], ["otc_and_rx_same_product"]]


def test_different_strengths_are_not_the_same_product():
    assert flags(candidate(strength="500 mg", reg_expiry="2030-01-01"),
                 candidate(strength="250 mg", classification="Rx", reg_expiry="2030-01-01")) == [[], []]


@pytest.mark.parametrize("changes, expected", [
    ({"export_only": True}, "export_only"),
    ({"reg_expiry": "2026-11-09"}, "expired"),
    ({"reg_expiry": "2027-01-15"}, "expires_within_90_days"),
    ({"fda_reg_no": "DRHR-1", "classification": "Rx"}, "drhr_mismatch"),
    ({"strength": "", "dosage_form": ""}, "unsplit_strength"),
])
def test_each_warning(changes, expected):
    assert expected in flags(candidate(**{"reg_expiry": "2030-01-01", **changes}))[0]


def test_expiring_today_is_within_90_days_not_expired():
    assert flags(candidate(reg_expiry="2026-11-10")) == [["expires_within_90_days"]]


def test_a_household_remedy_with_a_drhr_number_is_fine():
    assert flags(candidate(fda_reg_no="DRHR-1", classification="HR", reg_expiry="2030-01-01")) == [[]]


def test_candidates_route_returns_flags(client, store):
    store.candidates = [candidate(export_only=True)]
    assert "export_only" in client.get(f"{PATH}/fda-candidates", headers=auth()).json()[0]["flags"]


def test_pick_copies_the_fda_values_as_an_edit(client, store):
    assert client.post(f"{PATH}/fda-pick", json={"candidate_id": 1}, headers=auth("Cy")).status_code == 200
    kind, key, changes, actor = store.calls[0]
    assert (kind, key, actor) == ("update", "testflu-500-tab", "Cy")
    assert changes == {"fda_reg_no": "DR-XY99999", "reg_expiry": FUTURE, "classification": "OTC",
                       "manufacturer": "Portal Co", "portal_checked_on": "2026-11-10"}


def test_export_only_cannot_be_picked(client, store):
    store.candidates = [candidate(export_only=True)]
    r = client.post(f"{PATH}/fda-pick", json={"candidate_id": 1}, headers=auth())
    assert r.status_code == 422 and "export" in r.json()["detail"]
    assert store.calls == []


def test_a_candidate_for_another_brand_cannot_be_picked(client, store):
    store.candidates = [candidate(brand_query="Other", brand_name="Other")]
    assert client.post(f"{PATH}/fda-pick", json={"candidate_id": 1}, headers=auth()).status_code == 422


def test_unknown_candidate_is_404(client):
    assert client.post(f"{PATH}/fda-pick", json={"candidate_id": 99}, headers=auth()).status_code == 404


def test_tables_are_the_three_dataset_tables():
    assert {t.name for t in TABLES.values()} == {"kb_products", "kb_ingredients", "kb_passages"}
