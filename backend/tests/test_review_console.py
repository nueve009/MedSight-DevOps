"""The local launcher and the page's files (no network, no browser)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import config
from scripts import review_console as rc


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ENABLED", False)
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", False)
    monkeypatch.setattr(config, "SUPABASE_URL", "https://project.test")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setattr(config, "REVIEW_DATABASE_URL", "postgresql://x")


@pytest.fixture
def client(settings):
    return TestClient(rc.build_app(), base_url="http://127.0.0.1:8100")


# --- where it listens ---

def test_it_listens_on_this_computer_by_default():
    assert rc.choose_host(None, None) == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "example.test"])
def test_a_setting_cannot_open_it_to_the_network(host):
    with pytest.raises(SystemExit, match="Refusing"):
        rc.choose_host(None, host)


def test_a_loopback_setting_is_fine():
    assert rc.choose_host(None, "localhost") == "localhost"


def test_only_an_explicit_flag_can_open_it():
    assert rc.choose_host("0.0.0.0", None) == "0.0.0.0"


def test_missing_settings_are_listed(monkeypatch, settings):
    assert rc.missing_settings() == []
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "")
    assert rc.missing_settings() == ["SUPABASE_ANON_KEY"]


# --- what it serves ---

def test_page_and_config_are_served_from_one_origin(client):
    assert "MedSight Review" in client.get("/review/").text
    assert client.get("/review/review.js").status_code == 200
    text = client.get("/review/config.js").text
    values = json.loads(re.search(r"= (\{.*\});", text).group(1))
    assert values == {"supabaseUrl": "https://project.test", "supabaseAnonKey": "public-anon-key", "apiBase": "",
                      "requireMfa": False}


def test_the_page_is_told_when_two_step_is_on(client, monkeypatch):
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", True)
    assert '"requireMfa": true' in client.get("/review/config.js").text


def test_the_launcher_is_off_by_default_and_the_flag_turns_it_on():
    assert rc.parse_args([]).two_step is False
    assert rc.parse_args(["--require-mfa"]).two_step is True


def test_the_launcher_says_which_mode_it_is_in():
    assert rc.sign_in_line(False) == "Two-step sign-in: OFF (local testing only; turn on before hosting)"
    assert rc.sign_in_line(True) == "Two-step sign-in: ON"


def test_the_page_is_strict_unless_told_otherwise():
    assert "CFG.requireMfa !== false" in page_script()


def test_no_secret_reaches_the_page(client, monkeypatch):
    monkeypatch.setattr(config, "REVIEW_DATABASE_URL", "postgresql://user:db-password@host/db")
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "jwt-secret-value")
    text = client.get("/review/config.js").text
    assert "db-password" not in text and "jwt-secret-value" not in text


def test_the_admin_routes_are_on_for_this_process(client):
    assert client.get("/admin/kb/queue").status_code == 401   # exists, and needs a reviewer


@pytest.mark.parametrize("path", ["/v2/identify", "/identify", "/health", "/docs", "/openapi.json", "/admin/answers"])
def test_it_serves_only_the_console(client, path):
    assert client.get(path).status_code == 404


def test_security_headers(client):
    headers = client.get("/review/").headers
    policy = headers["content-security-policy"]
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "connect-src 'self' https://project.test" in policy
    assert headers["x-frame-options"] == "DENY"


def test_another_host_header_is_refused(settings):
    # Blocks a web page from pointing its own domain name at this computer.
    other = TestClient(rc.build_app(), base_url="http://attacker.test")
    assert other.get("/review/").status_code == 400


def test_evidence_uploads_have_a_body_limit(client):
    big = b"\xff\xd8\xff" + b"0" * (config.MAX_EVIDENCE_BODY_BYTES + 10)
    r = client.post("/admin/kb/products/x/evidence", content=big, headers={"Content-Type": "multipart/form-data"})
    assert r.status_code == 413


# --- the page's code ---

def page_script() -> str:
    return (rc.PAGE_DIR / "review.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("risky", ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("])
def test_the_page_never_builds_html_from_text(risky):
    assert risky not in page_script()


def test_the_page_loads_nothing_from_outside():
    html = (rc.PAGE_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"""(?:src|href)=["']https?:""", html)


def test_hosted_page_has_the_security_headers():
    headers = json.loads((rc.PAGE_DIR / "vercel.json").read_text(encoding="utf-8"))["headers"][0]["headers"]
    values = {h["key"]: h["value"] for h in headers}
    assert "frame-ancestors 'none'" in values["Content-Security-Policy"]
    assert "default-src 'self'" in values["Content-Security-Policy"]
    assert values["X-Frame-Options"] == "DENY" and "max-age" in values["Strict-Transport-Security"]
