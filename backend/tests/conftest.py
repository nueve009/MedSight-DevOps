"""
Shared test setup.

- Every test gets its own temp folder for data and logs, so nothing is written
  into the repo.
- llm_client.chat is replaced in every test. A test that doesn't install a
  fake reply fails loudly instead of calling a real LLM.
"""
import json

import pytest

from app import config, llm_client


class FakeLLM:
    """Stands in for llm_client.chat. Replies are chosen by the system prompt."""

    def __init__(self):
        self.replies: dict[str, list[str]] = {}
        self.calls: list[dict] = []

    def reply(self, system_prompt: str, *replies) -> None:
        """Queue replies (dicts are JSON-encoded) for calls with this system prompt.
        The last reply repeats once the queue is used up."""
        self.replies[system_prompt] = [r if isinstance(r, str) else json.dumps(r) for r in replies]

    def raise_error(self, system_prompt: str, error: Exception) -> None:
        self.replies[system_prompt] = [error]

    def count(self, system_prompt: str) -> int:
        return sum(1 for c in self.calls if c["system"] == system_prompt)

    def __call__(self, system, user, schema=None, max_tokens=300):
        self.calls.append({"system": system, "user": user})
        queue = self.replies.get(system)
        if not queue:
            raise AssertionError(f"Unexpected LLM call (no fake reply set): {system[:60]!r}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CACHE_DB_PATH", data_dir / "cache.sqlite")
    monkeypatch.setattr(config, "KB_DB_PATH", data_dir / "kb.sqlite")
    monkeypatch.setattr(config, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(config, "RUN_LOG_PATH", logs_dir / "run_log.jsonl")
    return tmp_path


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    """Tests don't depend on whatever a developer's .env sets."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_MODEL", "test-model")
    monkeypatch.setattr(config, "OCR_CAVEAT_THRESHOLD", 0.55)
    monkeypatch.setattr(config, "MAX_SCAN_ATTEMPTS", 3)
    monkeypatch.setattr(config, "MAX_PARSE_CHARS", 2000)


@pytest.fixture
def cache(tmp_path):
    from app.cache_store import CacheStore
    return CacheStore(tmp_path / "cache.sqlite")


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "chat", fake)
    return fake
