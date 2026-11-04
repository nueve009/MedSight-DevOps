"""Admin routes (M7): per-person keys, the audit log, and what admins can't do."""
import logging
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import config, description_agent, llm_parse, speech
from app.api import app
from app.store import StoreUnavailable
from app.utils import answer_key
from tests.fixtures import labels

APP_KEY = "app-key"
MON, ANA = {"X-Admin-Key": "mon-secret"}, {"X-Admin-Key": "ana-secret"}
PRODUCT = labels.BIOGESIC
KEY = answer_key("Biogesic", ["Paracetamol"], ["500 mg"])
TFD = "https://www.thefilipinodoctor.com/drug/biogesic"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", APP_KEY)
    monkeypatch.setattr(config, "ADMIN_ENABLED", True)
    monkeypatch.setattr(config, "ADMIN_KEYS", "mon=mon-secret, ana=ana-secret")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    with TestClient(app) as c:
        yield c
    root.handlers, root.level = saved_handlers, saved_level


@pytest.fixture
def web_answer(client):
    client.app.state.cache.set_answer(KEY, "web", PRODUCT, "Relieves pain and fever.", [TFD],
                                      status="pending_review")
    return KEY


# --- access ---

@pytest.mark.parametrize("method, path", [("get", "/admin/answers"), ("get", f"/admin/answers/{KEY}"),
                                          ("patch", f"/admin/answers/{KEY}"), ("delete", f"/admin/answers/{KEY}"),
                                          ("get", "/admin/audit"), ("get", "/admin")])
def test_every_admin_path_is_404_when_disabled(client, monkeypatch, method, path):
    monkeypatch.setattr(config, "ADMIN_ENABLED", False)
    resp = client.request(method, path, headers=MON, json={"status": "active"})
    assert resp.status_code == 404


def test_admin_and_docs_are_off_by_default():
    """With nothing set (and no .env), both default to off."""
    env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_ENABLED", "API_DOCS")}
    code = ("import dotenv; dotenv.load_dotenv = lambda *a, **k: None; "
            "from app import config; print(config.ADMIN_ENABLED, config.API_DOCS)")
    out = subprocess.run([sys.executable, "-c", code], cwd=config.BASE_DIR, env=env,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False"


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": APP_KEY},
                                     {"X-API-Key": APP_KEY}])
def test_wrong_missing_or_app_keys_are_refused(client, web_answer, headers):
    assert client.get("/admin/answers", headers=headers).status_code == 401


def test_an_admin_key_equal_to_an_app_key_never_works(client, monkeypatch, caplog):
    monkeypatch.setattr(config, "ADMIN_KEYS", f"mon=mon-secret,sneaky={APP_KEY}")
    with caplog.at_level(logging.WARNING):
        assert client.get("/admin/answers", headers={"X-Admin-Key": APP_KEY}).status_code == 401
    assert "ignored" in caplog.text
    assert client.get("/admin/answers", headers=MON).status_code == 200


def test_no_admin_keys_is_a_503(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_KEYS", "")
    assert client.get("/admin/answers", headers=MON).status_code == 503


def test_the_admin_key_doesnt_work_for_scans(client):
    assert client.post("/v2/identify", json={"text": "x"}, headers={"X-API-Key": "mon-secret"}).status_code == 401


# --- reading ---

def test_list_and_read(client, web_answer):
    listed = client.get("/admin/answers?status=pending_review", headers=MON).json()["answers"]
    assert [a["cache_key"] for a in listed] == [KEY]
    assert client.get("/admin/answers?status=active", headers=MON).json()["answers"] == []
    one = client.get(f"/admin/answers/{KEY}", headers=MON).json()
    assert (one["tier"], one["status"], one["sources"]) == ("web", "pending_review", [TFD])
    assert client.get("/admin/answers/nope", headers=MON).status_code == 404


# --- writes and the audit log ---

def test_approve_is_audited_with_the_admins_name(client, web_answer):
    resp = client.patch(f"/admin/answers/{KEY}", headers=MON, json={"status": "active"})
    assert resp.status_code == 200
    assert (resp.json()["before"]["status"], resp.json()["after"]["status"]) == ("pending_review", "active")
    (entry,) = client.get("/admin/audit", headers=ANA).json()["audit"]
    assert (entry["admin"], entry["action"], entry["cache_key"]) == ("mon", "update", KEY)
    assert "mon-secret" not in str(entry)


def test_reject_makes_the_next_scan_search_again(client, web_answer, fake_llm, fake_search, monkeypatch):
    client.patch(f"/admin/answers/{KEY}", headers=MON, json={"status": "rejected"})
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "k")
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT,
                   {"description": "A pain reliever and fever reducer.", "confidence": 0.5})
    fake_search.results = []
    body = client.post("/v2/identify", json={"text": labels.BIOGESIC_TEXT}, headers={"X-API-Key": APP_KEY}).json()
    assert body["tier"] == "general"
    assert len(fake_search.queries) == 1


def test_fixing_the_text_is_spoken_on_the_next_scan(client, web_answer, fake_llm):
    fixed = "Used to relieve pain and reduce fever."
    assert client.patch(f"/admin/answers/{KEY}", headers=ANA,
                        json={"description": fixed, "status": "active"}).status_code == 200
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    body = client.post("/v2/identify", json={"text": labels.BIOGESIC_TEXT}, headers={"X-API-Key": APP_KEY}).json()
    assert (body["tier"], body["description"], body["cache"]) == ("web", fixed, "hit")
    assert speech.WEB_WARNING in body["spoken_text"]   # an approved web answer keeps its warning
    assert fake_llm.count(description_agent.DESCRIPTION_SYSTEM_PROMPT) == 0


def test_delete_is_audited_and_the_next_scan_answers_afresh(client, web_answer):
    assert client.delete(f"/admin/answers/{KEY}", headers=MON).json()["deleted"]["cache_key"] == KEY
    assert client.get(f"/admin/answers/{KEY}", headers=MON).status_code == 404
    assert client.delete(f"/admin/answers/{KEY}", headers=MON).status_code == 404
    (entry,) = client.get("/admin/audit", headers=MON).json()["audit"]
    assert (entry["action"], entry["after"]) == ("delete", None)


# --- what admins can't do ---

@pytest.mark.parametrize("body", [
    {"tier": "reviewed"},
    {"tier": "reviewed", "status": "active"},
    {"status": "verified"},
    {"status": "active", "sources": ["https://evil.test"]},
    {},
    {"description": "Take 2 tablets every 4 hours."},
    {"description": "The maximum dose is 4 g a day."},
    {"description": "   "},
    {"description": "Pain relief. " * 30},
])
def test_refused_changes_leave_no_trace(client, web_answer, body):
    assert client.patch(f"/admin/answers/{KEY}", headers=MON, json=body).status_code == 422
    assert client.get(f"/admin/answers/{KEY}", headers=MON).json()["status"] == "pending_review"
    assert client.get("/admin/audit", headers=MON).json()["audit"] == []


def test_a_database_outage_is_reported_not_hidden(client, web_answer, monkeypatch):
    def down(*a, **k):
        raise StoreUnavailable("down")
    monkeypatch.setattr(client.app.state.cache, "admin_update_answer", down)
    resp = client.patch(f"/admin/answers/{KEY}", headers=MON, json={"status": "active"})
    assert resp.status_code == 503
    assert "nothing was changed" in resp.json()["detail"]
