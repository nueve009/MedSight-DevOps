"""The hosted review service (app/review_service.py): settings it refuses to
start without, what it serves, and CORS for the hosted page only."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, review_service

PAGE = "https://medsight-review.vercel.app"


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ENABLED", False)
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", True)
    monkeypatch.setattr(config, "SUPABASE_URL", "https://project.test")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setattr(config, "REVIEW_DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(config, "REVIEW_ALLOWED_ORIGINS", [PAGE])


@pytest.fixture
def client(settings):
    return TestClient(review_service.build_app())


@pytest.mark.parametrize("name, value, message", [
    ("REVIEW_DATABASE_URL", "", "REVIEW_DATABASE_URL"),
    ("SUPABASE_ANON_KEY", "", "SUPABASE_ANON_KEY"),
    ("REVIEW_ALLOWED_ORIGINS", [], "REVIEW_ALLOWED_ORIGINS"),
    ("REVIEW_ALLOWED_ORIGINS", ["http://medsight-review.vercel.app"], "https"),
    ("REVIEW_ALLOWED_ORIGINS", [PAGE + "/"], "trailing slash"),
])
def test_it_refuses_to_start_without_safe_settings(settings, monkeypatch, name, value, message):
    monkeypatch.setattr(config, name, value)
    with pytest.raises(review_service.NotReady, match=message):
        review_service.build_app()


def test_health_and_commit(client, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567")
    assert client.get("/health").json() == {"status": "ok", "service": "review", "two_step": "on",
                                            "commit": "abcdef1"}


def test_two_step_can_be_switched_off_but_not_quietly(settings, monkeypatch, caplog):
    """Mon, 2026-11-14: off for the demo. It starts, warns in the log and says so on /health.
    (What sign-in accepts with the switch off is tested in test_review_auth.py.)"""
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", False)
    with caplog.at_level("WARNING"):
        client = TestClient(review_service.build_app())
    assert "two-step sign-in is OFF" in caplog.text
    assert client.get("/health").json()["two_step"] == "off"


def test_review_routes_are_on_and_need_a_reviewer(client):
    assert client.get("/admin/kb/queue").status_code == 401


@pytest.mark.parametrize("path", ["/v2/identify", "/identify", "/admin/answers", "/docs", "/openapi.json",
                                  "/review/"])
def test_nothing_else_is_served(client, path):
    assert client.get(path).status_code == 404


def test_cors_allows_only_the_hosted_page(client):
    ok = client.options("/admin/kb/queue", headers={"Origin": PAGE, "Access-Control-Request-Method": "GET",
                                                    "Access-Control-Request-Headers": "authorization"})
    assert ok.headers.get("access-control-allow-origin") == PAGE
    other = client.options("/admin/kb/queue", headers={"Origin": "https://evil.test",
                                                       "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in other.headers


def test_vercel_build_writes_the_page_config(tmp_path):
    page_dir = Path(review_service.__file__).resolve().parent.parent / "review"
    for name in ("build.mjs", "index.html", "review.css", "review.js"):
        (tmp_path / name).write_bytes((page_dir / name).read_bytes())
    if shutil.which("node") is None:
        pytest.skip("node isn't installed")
    base = {k: v for k, v in os.environ.items() if not k.startswith("REVIEW_")}
    env = {**base, "REVIEW_SUPABASE_URL": "https://p.supabase.co/",
           "REVIEW_SUPABASE_ANON_KEY": "anon", "REVIEW_API_BASE": "https://r.up.railway.app"}
    done = subprocess.run(["node", "build.mjs"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    text = (tmp_path / "dist" / "config.js").read_text()
    assert json.loads(text.split("= ", 1)[1].rstrip(";\n")) == {
        "supabaseUrl": "https://p.supabase.co", "supabaseAnonKey": "anon",
        "apiBase": "https://r.up.railway.app", "requireMfa": True}
    missing = subprocess.run(["node", "build.mjs"], cwd=tmp_path, env=base,
                             capture_output=True, text=True)
    assert missing.returncode == 1 and "REVIEW_API_BASE" in missing.stderr

    for value, expected in (("off", False), ("OFF", False), ("on", True), ("garbage", True)):
        done = subprocess.run(["node", "build.mjs"], cwd=tmp_path, env={**env, "REVIEW_REQUIRE_MFA": value},
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        built = json.loads((tmp_path / "dist" / "config.js").read_text().split("= ", 1)[1].rstrip(";\n"))
        assert built["requireMfa"] is expected, value
