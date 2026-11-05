"""Failure drills (plan M7, pilot gate): each outage ends in a clear spoken
message, never a made-up answer or a bare error. The same drills are run by
hand on staging (docs/runbook.md, "Failure drills")."""
import logging

import pytest
from fastapi.testclient import TestClient

from app import config, description_agent, llm_parse, speech, web_search
from app.api import app
from app.llm_client import LLMUnavailableError
from app.store import StoreUnavailable
from tests.fixtures import labels

PARSE, DESCRIBE = llm_parse.PARSE_SYSTEM_PROMPT, description_agent.DESCRIPTION_SYSTEM_PROMPT


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEYS", "release-1=old-key")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    with TestClient(app) as c:
        yield c
    root.handlers, root.level = saved_handlers, saved_level


def scan(client, key="old-key", text=labels.BIOGESIC_TEXT, device="drill-phone"):
    return client.post("/v2/identify", json={"text": text}, headers={"X-API-Key": key, "X-Device-Id": device})


@pytest.fixture
def biogesic(fake_llm):
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, {"description": "A pain reliever and fever reducer.", "confidence": 0.5})
    return fake_llm


class DeadStore:
    """Every call fails, as when Supabase is down."""

    def close(self):
        pass

    def __getattr__(self, name):
        def fail(*a, **k):
            raise StoreUnavailable("down")
        return fail


def test_drill_groq_down(client, fake_llm):
    fake_llm.raise_error(PARSE, LLMUnavailableError("timeout"))
    resp = scan(client)
    assert resp.status_code == 503
    assert resp.json()["spoken_text"] == speech.SERVICE_BUSY
    assert resp.json()["request_id"]


def test_drill_groq_down_after_the_parse(client, fake_llm):
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.raise_error(DESCRIBE, LLMUnavailableError("HTTP 500"))
    resp = scan(client)
    assert resp.status_code == 503 and resp.json()["spoken_text"] == speech.SERVICE_BUSY


def test_drill_search_down(client, biogesic, fake_search, monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "k")
    fake_search.error = web_search.WebSearchUnavailable("HTTP 503")
    body = scan(client).json()
    assert (body["status"], body["tier"]) == ("ok", "general")
    assert body["spoken_text"].index(speech.GENERAL_WARNING) < body["spoken_text"].index("pain reliever")


def test_drill_supabase_down(client, biogesic):
    client.app.state.cache = DeadStore()
    client.app.state.limiter.store = client.app.state.cache
    body = scan(client).json()
    assert (body["status"], body["tier"], body["cache"]) == ("ok", "general", "miss")
    # The rate limits keep working from memory.
    for _ in range(config.RATE_DEVICE_PER_MINUTE - 1):
        scan(client, text="   ")
    limited = scan(client, text="   ")
    assert limited.status_code == 429 and limited.json()["spoken_text"] == speech.TOO_FAST


def test_drill_key_rotated(client, biogesic, monkeypatch):
    assert scan(client).status_code == 200
    # A new release ships "new-key"; for a while both work, then the old one is removed.
    monkeypatch.setattr(config, "MEDSIGHT_API_KEYS", "release-1=old-key,release-2=new-key")
    assert scan(client, key="new-key").status_code == 200
    monkeypatch.setattr(config, "MEDSIGHT_API_KEYS", "release-2=new-key")
    assert scan(client).status_code == 401
    assert scan(client, key="new-key").status_code == 200


def test_drill_model_budget_used_up(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET", 0)
    resp = scan(client)
    assert resp.status_code == 429
    assert (resp.json()["reason"], resp.json()["spoken_text"]) == ("service_limit", speech.SERVICE_LIMIT)
    assert fake_llm.calls == []
