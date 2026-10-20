"""API tests through FastAPI's TestClient. The LLM is always the fake from conftest."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import config, description_agent, llm_parse
from app.api import app
from app.llm_client import LLMUnavailableError
from app.logging_setup import JsonFormatter, request_id_var

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}
BIOGESIC_PARSE = {"generic_name": "Paracetamol", "brand_name": "Biogesic",
                  "dosage": "500 mg", "confidence": 0.95}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", API_KEY)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    with TestClient(app) as c:
        yield c
    root.handlers, root.level = saved_handlers, saved_level


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_identify_rejects_missing_or_wrong_key(client, headers):
    assert client.post("/identify", json={"text": "x"}, headers=headers).status_code == 401


def test_identify_fails_closed_without_server_key(client, monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", "")
    assert client.post("/identify", json={"text": "x"}, headers=HEADERS).status_code == 503


def test_identify_rejects_oversized_text(client):
    resp = client.post("/identify", json={"text": "a" * (config.MAX_OCR_TEXT_CHARS + 1)}, headers=HEADERS)
    assert resp.status_code == 422


def test_empty_text_is_rescan_without_llm_call(client, fake_llm):
    resp = client.post("/identify", json={"text": "  "}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rescan"
    assert fake_llm.calls == []


def test_identify_ok(client, fake_llm):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, BIOGESIC_PARSE)
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT,
                   {"description": "A pain reliever and fever reducer.", "confidence": 0.5})
    body = client.post("/identify", json={"text": "BIOGESIC\nParacetamol 500 mg"}, headers=HEADERS).json()
    assert body["status"] == "ok"
    assert body["brand_name"] == "Biogesic"
    assert body["grounded"] is False
    # The unverified warning comes before the description.
    assert body["spoken_text"].index("not in my verified list") < body["spoken_text"].index("pain reliever")


def test_llm_down_returns_503_with_spoken_text(client, fake_llm):
    fake_llm.raise_error(llm_parse.PARSE_SYSTEM_PROMPT, LLMUnavailableError("down"))
    resp = client.post("/identify", json={"text": "BIOGESIC"}, headers=HEADERS)
    assert resp.status_code == 503
    assert "try again" in resp.json()["spoken_text"]


def test_request_id_is_generated(client):
    resp = client.get("/health")
    assert len(resp.headers["X-Request-Id"]) == 32


def test_valid_client_request_id_is_kept(client):
    assert client.get("/health", headers={"X-Request-Id": "abc-123"}).headers["X-Request-Id"] == "abc-123"


def test_unsafe_client_request_id_is_replaced(client):
    rid = client.get("/health", headers={"X-Request-Id": 'x" "level": "error'}).headers["X-Request-Id"]
    assert rid != 'x" "level": "error'
    assert len(rid) == 32


def test_request_id_reaches_pipeline_logs(client, fake_llm):
    """The sync endpoint runs in a worker thread; its log lines must still carry the ID."""
    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(request_id_var.get())

    handler = Collect()
    logging.getLogger("app.pipeline").addHandler(handler)
    try:
        client.post("/identify", json={"text": ""}, headers={**HEADERS, "X-Request-Id": "scan-42"})
    finally:
        logging.getLogger("app.pipeline").removeHandler(handler)
    assert seen == ["scan-42"]


def test_json_formatter_includes_request_id_and_fields():
    token = request_id_var.set("rid-1")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "scan", None, None)
        record.fields = {"status": "ok", "brand": "Biogésic"}
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    assert line.isascii()
    entry = json.loads(line)
    assert entry["request_id"] == "rid-1"
    assert entry["msg"] == "scan"
    assert entry["status"] == "ok"
