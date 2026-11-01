"""Rate limits, the daily LLM budget, app keys and the concurrent-scan cap."""
import logging

import pytest
from fastapi.testclient import TestClient

from app import config, description_agent, llm_parse, speech
from app.api import app, client_address
from app.ratelimit import DAY, Limiter, LLMBudgetExceeded, MemoryCounters, RateLimited, parse_pairs
from app.store import StoreUnavailable
from app.store.sqlite import SQLiteStore
from tests.fixtures import labels

PARSE, DESCRIBE = llm_parse.PARSE_SYSTEM_PROMPT, description_agent.DESCRIPTION_SYSTEM_PROMPT
KEY = "test-key"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):   # a fixed instant, mid-minute
        self.t = t + 10

    def __call__(self) -> float:
        return self.t


class DeadStore:
    def incr_counters(self, items):
        raise StoreUnavailable("down")


@pytest.fixture
def limiter(tmp_path):
    return Limiter(SQLiteStore(tmp_path / "cache.sqlite"), clock=Clock())


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", KEY)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    with TestClient(app) as c:
        yield c
    root.handlers, root.level = saved_handlers, saved_level


def post(client, device="phone-1", key=KEY, path="/v2/identify"):
    return client.post(path, json={"text": labels.BIOGESIC_TEXT}, headers={"X-API-Key": key, "X-Device-Id": device})


# --- Limiter ---

def test_device_minute_limit_then_the_next_minute_is_open(limiter, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 3)
    for _ in range(3):
        limiter.check_request("phone", "1.2.3.4")
    with pytest.raises(RateLimited) as e:
        limiter.check_request("phone", "1.2.3.4")
    assert (e.value.reason, e.value.retry_after_s) == ("too_fast", 50)
    limiter.check_request("other-phone", "1.2.3.4")   # another device is still fine
    limiter.clock.t += 60
    limiter.check_request("phone", "1.2.3.4")


def test_daily_device_limit_says_try_tomorrow(limiter, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_DAY", 2)
    limiter.check_request("phone", "a")
    limiter.clock.t += 120
    limiter.check_request("phone", "a")
    with pytest.raises(RateLimited) as e:
        limiter.check_request("phone", "a")
    assert e.value.reason == "daily_limit" and e.value.retry_after_s > 3600


def test_address_limit_catches_rotating_device_ids(limiter, monkeypatch):
    monkeypatch.setattr(config, "RATE_ADDRESS_PER_MINUTE", 5)
    for i in range(5):
        limiter.check_request(f"fake-device-{i}", "9.9.9.9")
    with pytest.raises(RateLimited):
        limiter.check_request("fake-device-99", "9.9.9.9")


def test_no_device_id_counts_per_address(limiter, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 2)
    limiter.check_request(None, "5.5.5.5")
    limiter.check_request(None, "5.5.5.5")
    with pytest.raises(RateLimited):
        limiter.check_request(None, "5.5.5.5")
    limiter.check_request(None, "6.6.6.6")


def test_llm_budget_global_and_per_key(limiter, monkeypatch):
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET", 3)
    monkeypatch.setattr(config, "KEY_DAILY_LLM_CAPS", "testers=1")
    limiter.spend_llm_call("testers")
    with pytest.raises(LLMBudgetExceeded):
        limiter.spend_llm_call("testers")             # the testers' own cap
    limiter.spend_llm_call("pitch")                   # global: 3rd call
    with pytest.raises(LLMBudgetExceeded) as e:
        limiter.spend_llm_call("pitch")               # global: 4th call
    assert 0 < e.value.retry_after_s <= DAY


def test_counters_fall_back_to_memory_when_the_store_is_down(monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 2)
    limiter = Limiter(DeadStore(), clock=Clock())
    limiter.check_request("phone", "a")
    limiter.check_request("phone", "a")
    with pytest.raises(RateLimited):      # still limited: a database outage never turns the limits off
        limiter.check_request("phone", "a")


def test_minute_and_day_windows_never_share_a_counter_at_midnight(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 100)
    limiter = Limiter(SQLiteStore(tmp_path / "c.sqlite"), clock=lambda: 1_800_028_800.0)   # 00:00 UTC
    limiter.check_request("phone", "a")   # would raise a primary-key clash if they shared a row


def test_memory_counters():
    m = MemoryCounters()
    assert m.incr_counters([("a", 0, 60), ("b", 0, 60), ("a", 0, 60)]) == [1, 1, 2]


def test_parse_pairs():
    assert parse_pairs(" pitch=abc , testers=x=y,, bad ,=nokey") == {"pitch": "abc", "testers": "x=y"}


# --- through the API ---

def test_a_script_flooding_one_device_gets_429s_and_spends_no_llm_calls(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 15)
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    codes = [post(client).status_code for _ in range(60)]
    assert codes[:15] == [200] * 15 and set(codes[15:]) == {429}
    assert fake_llm.count(PARSE) + fake_llm.count(DESCRIBE) <= 2   # repeats are cache hits; refused ones cost nothing

    r = post(client)
    body = r.json()
    assert (body["status"], body["reason"], body["spoken_text"]) == ("rate_limited", "too_fast", speech.TOO_FAST)
    assert int(r.headers["Retry-After"]) == body["retry_after_s"] > 0
    assert body["request_id"] == r.headers["X-Request-Id"]


def test_normal_use_is_never_blocked(client, fake_llm):
    """A rescan every 5 seconds is 12 a minute, under the default 15."""
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    assert all(post(client).status_code == 200 for _ in range(12))


def test_v1_is_limited_too(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 1)
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    assert post(client, path="/identify").status_code == 200
    assert post(client, path="/identify").status_code == 429


def test_wrong_keys_are_refused_before_counting(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_DEVICE_PER_MINUTE", 1)
    for _ in range(3):
        assert post(client, key="wrong").status_code == 401


def test_daily_llm_budget_returns_429_with_spoken_text(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET", 2)    # one scan = parse + describe
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    assert post(client).status_code == 200
    r = client.post("/v2/identify", json={"text": "NEOZEP FORTE\nPhenylephrine"},
                    headers={"X-API-Key": KEY, "X-Device-Id": "phone-1"})
    assert r.status_code == 429
    assert (r.json()["reason"], r.json()["spoken_text"]) == ("service_limit", speech.SERVICE_LIMIT)
    assert post(client).status_code == 200   # a fully cached scan needs no model call


def test_several_named_keys(client, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", "")
    monkeypatch.setattr(config, "MEDSIGHT_API_KEYS", "pitch=pitch-key, testers=tester-key")
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    assert post(client, key="pitch-key").status_code == 200
    assert post(client, key="tester-key", device="p2").status_code == 200
    assert post(client, key="test-key", device="p3").status_code == 401   # retired


def test_no_keys_configured_fails_closed(client, monkeypatch):
    monkeypatch.setattr(config, "MEDSIGHT_API_KEY", "")
    monkeypatch.setattr(config, "MEDSIGHT_API_KEYS", "")
    assert post(client).status_code == 503


def test_too_many_scans_at_once_get_a_quick_503(client, fake_llm):
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    slots = app.state.scan_slots
    for _ in range(config.MAX_CONCURRENT_SCANS):   # every slot taken by other scans
        slots.acquire()
    try:
        r = post(client)
        assert r.status_code == 503 and r.headers["Retry-After"] == "2"
        assert r.json()["spoken_text"] == speech.SERVICE_BUSY
    finally:
        for _ in range(config.MAX_CONCURRENT_SCANS):
            slots.release()
    assert post(client).status_code == 200


@pytest.mark.parametrize("header, headers, peer_expected", [
    ("", {"x-real-ip": "1.1.1.1"}, True),                       # header ignored unless configured
    ("x-real-ip", {"x-real-ip": "1.1.1.1"}, False),
    ("x-forwarded-for", {"x-forwarded-for": "6.6.6.6, 2.2.2.2"}, False),   # the proxy's entry, not the client's
    ("x-real-ip", {}, True),
])
def test_client_address(monkeypatch, header, headers, peer_expected):
    from starlette.requests import Request
    monkeypatch.setattr(config, "CLIENT_IP_HEADER", header)
    scope = {"type": "http", "client": ("10.0.0.1", 1234),
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    got = client_address(Request(scope))
    if peer_expected:
        assert got == "10.0.0.1"
    else:
        assert got == {"x-real-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"}[header]
