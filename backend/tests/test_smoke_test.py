"""scripts/smoke_test.py against the real app through TestClient (fake LLM),
and the /docs gate it checks."""
import logging

import pytest
from fastapi.testclient import TestClient

from app import config, description_agent, llm_parse
from app.api import app
from scripts import smoke_test
from tests.fixtures import labels

KEY = "test-key"
BASE = "http://testserver"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", KEY)
    monkeypatch.setattr(config, "API_DOCS", False)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    with TestClient(app) as c:
        yield c
    root.handlers, root.level = saved_handlers, saved_level


@pytest.fixture
def biogesic(fake_llm):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT,
                   {"status": "ok", "description": "Used to relieve mild pain and reduce fever."})
    return fake_llm


def results(checks) -> dict[str, bool]:
    return {c.name: c.ok for c in checks}


def test_smoke_test_text_matches_the_fixture():
    assert smoke_test.KNOWN_SCAN_TEXT == labels.BIOGESIC_TEXT


def test_every_check_passes_against_a_healthy_service(client, biogesic):
    checks = smoke_test.run(client, BASE, KEY, timeout_s=None)
    assert results(checks) == {"health": True, "wrong key": True, "empty text": True,
                               "known scan": True, "docs off": True, "rate limit": True}
    assert biogesic.count(llm_parse.PARSE_SYSTEM_PROMPT) == 1   # only the known scan calls the model


def test_rate_limit_check_stops_at_the_first_429(client, biogesic, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 4)
    rate = next(c for c in smoke_test.run(client, BASE, KEY, timeout_s=None) if c.name == "rate limit")
    assert rate.ok
    assert "429 after 3 more scans" in rate.detail   # 2 earlier scans + 2 more fill the 4; the third is refused
    assert "reason=too_fast" in rate.detail


def test_docs_left_on_fails_the_check(client, biogesic, monkeypatch):
    monkeypatch.setattr(config, "API_DOCS", True)
    assert results(smoke_test.run(client, BASE, KEY, timeout_s=None))["docs off"] is False


def test_unconfigured_server_key_fails(client, biogesic, monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", "")
    res = results(smoke_test.run(client, BASE, KEY, timeout_s=None))
    assert res["health"] is True
    assert res["wrong key"] is False and res["known scan"] is False


def test_model_down_fails_the_known_scan(client, fake_llm):
    from app.llm_client import LLMUnavailableError
    fake_llm.raise_error(llm_parse.PARSE_SYSTEM_PROMPT, LLMUnavailableError("down"))
    known = next(c for c in smoke_test.run(client, BASE, KEY, timeout_s=None) if c.name == "known scan")
    assert not known.ok and known.detail.startswith("503")


def test_main_needs_a_key(monkeypatch, capsys):
    monkeypatch.delenv("SMOKE_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["smoke_test", "--url", BASE])
    assert smoke_test.main() == 1
    assert "SMOKE_API_KEY" in capsys.readouterr().err


# --- the /docs gate ---

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_docs_are_off_by_default(client, path):
    assert client.get(path).status_code == 404


def test_docs_can_be_turned_on(client, monkeypatch):
    monkeypatch.setattr(config, "API_DOCS", True)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "MedSight backend"

