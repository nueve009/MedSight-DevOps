"""API tests through FastAPI's TestClient. The LLM is always the fake from conftest."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import config, description_agent, llm_parse
from app.api import app
from app.llm_client import LLMUnavailableError
from app.logging_setup import JsonFormatter, request_id_var
from tests.fixtures import labels

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}
BIOGESIC_PARSE = labels.reply(labels.BIOGESIC)


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
    assert body["warnings"] == ["not verified"]
    # The warning comes before the description.
    assert body["spoken_text"].index("isn't in our reviewed list") < body["spoken_text"].index("pain reliever")


# --- v2 ---

def test_v2_answer_shape(client, fake_llm):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, BIOGESIC_PARSE)
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)
    resp = client.post("/v2/identify", headers={**HEADERS, "X-Device-Id": "3f2a-device", "X-Request-Id": "r-1"},
                       json={"text": labels.BIOGESIC_TEXT, "ocr_confidence": 0.9, "attempt": 1,
                             "blocks": [{"text": "BIOGESIC", "confidence": 0.93, "box": [0.1, 0.3, 0.5, 0.4]}],
                             "capture_hints": {"blurry": False, "dark": False, "text_small": False},
                             "platform": "android", "app_version": "1.2.0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "ok", "rescan_reason": None, "hint": None, "tier": "general",
        "product": {"brand": "Biogesic", "generics": ["Paracetamol"], "strengths": ["500 mg"],
                    "prescription": False},
        "description": "A pain reliever and fever reducer.", "sources": [],
        "spoken_text": body["spoken_text"], "request_id": "r-1", "cache": "miss",
    }


def test_v2_text_falls_back_to_blocks(client, fake_llm):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC, labels.ADVIL))
    blocks = [{"text": line} for line in labels.TWO_BOXES_TEXT.split("\n")]
    body = client.post("/v2/identify", json={"blocks": blocks}, headers=HEADERS).json()
    assert body["status"] == "multiple"


def test_v2_rescan_with_hint(client, fake_llm):
    body = client.post("/v2/identify", json={"text": "", "capture_hints": {"dark": True}}, headers=HEADERS).json()
    assert (body["status"], body["rescan_reason"], body["hint"]) == ("rescan", "no_text", "more_light")
    assert fake_llm.calls == []


@pytest.mark.parametrize("body", [
    {"blocks": [{"text": "x"}] * (config.MAX_BLOCKS + 1)},
    {"text": "a" * (config.MAX_OCR_TEXT_CHARS + 1)},
    {"text": "BIOGESIC", "attempt": 0},
    {"text": "BIOGESIC", "ocr_confidence": 1.5},
    {"text": "BIOGESIC", "blocks": [{"text": "x", "box": [0.1, 0.2]}]},
])
def test_v2_rejects_invalid_requests(client, body):
    assert client.post("/v2/identify", json=body, headers=HEADERS).status_code == 422


def test_v2_requires_key(client):
    assert client.post("/v2/identify", json={"text": "x"}).status_code == 401


def test_body_over_limit_is_413_with_request_id(client):
    resp = client.post("/v2/identify", content=b'{"text": "' + b"a" * config.MAX_BODY_BYTES + b'"}',
                       headers={**HEADERS, "Content-Type": "application/json"})
    assert resp.status_code == 413
    assert resp.headers["X-Request-Id"]


def test_body_over_limit_without_content_length_is_413(client):
    def chunks():
        yield b'{"text": "'
        for _ in range(70):
            yield b"a" * 1024
        yield b'"}'
    resp = client.post("/v2/identify", content=chunks(), headers={**HEADERS, "Content-Type": "application/json"})
    assert resp.status_code == 413


def test_v2_503_carries_request_id(client, fake_llm):
    fake_llm.raise_error(llm_parse.PARSE_SYSTEM_PROMPT, LLMUnavailableError("down"))
    resp = client.post("/v2/identify", json={"text": "BIOGESIC"}, headers={**HEADERS, "X-Request-Id": "r-9"})
    assert resp.status_code == 503
    assert resp.json()["request_id"] == "r-9"


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
