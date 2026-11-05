"""
Smoke test for a deployed backend: run it after each deploy to staging, and
again after promoting to production (plan section 9, release steps).

Usage (from backend/):
    $env:SMOKE_API_KEY = "<an app key for that environment>"
    python -m scripts.smoke_test --url https://<service host>

The key comes from SMOKE_API_KEY, never the command line, so it doesn't end
up in shell history or CI logs.

Checks, in order:
    1. health      GET /health answers 200 with status "ok"
    2. wrong key   a wrong X-API-Key gets 401
    3. empty text  blank text is a "no_text" rescan (no model call)
    4. known scan  a Biogesic label answers ok with spoken text
    5. docs off    /docs and /openapi.json are 404
    6. rate limit  repeated scans end in 429 with Retry-After and spoken text

Each run uses a new random device ID. Only check 4 can call the model (at
most twice; repeat runs hit the cache). Check 6 sends empty-text scans, which
cost nothing but use up this address's limit for the rest of the minute, so
wait a minute before running it again.

Exit code 0 when every check passes, 1 otherwise.
"""
import argparse
import os
import sys
import uuid
from dataclasses import dataclass

import requests

KNOWN_SCAN_TEXT = "BIOGESIC\nParacetamol\n500 mg Tablet\nFor the relief of minor aches and pains\nUNILAB"
WRONG_KEY = "smoke-test-wrong-key"
MAX_RATE_LIMIT_TRIES = 60   # above the default limits (15 a device, 30 an address, per minute)
TIMEOUT_S = 20              # a little over the server's LLM timeout


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _json(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def run(client, base_url: str, api_key: str, device_id: str | None = None,
        timeout_s: float | None = TIMEOUT_S) -> list[Check]:
    """Runs every check against base_url. client has requests' get/post
    interface (a requests.Session, or FastAPI's TestClient in the tests, which
    takes no timeout)."""
    kw = {"timeout": timeout_s} if timeout_s else {}
    base_url = base_url.rstrip("/")
    device_id = device_id or f"smoke-{uuid.uuid4()}"
    headers = {"X-API-Key": api_key, "X-Device-Id": device_id}
    checks: list[Check] = []

    def scan(text: str, key_headers: dict):
        return client.post(f"{base_url}/v2/identify", json={"text": text, "attempt": 1},
                           headers=key_headers, **kw)

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name, ok, detail))

    def guarded(name: str, step) -> None:
        try:
            step()
        except requests.RequestException as e:
            add(name, False, f"request failed: {type(e).__name__}")

    def health():
        resp = client.get(f"{base_url}/health", **kw)
        body = _json(resp)
        add("health", resp.status_code == 200 and body.get("status") == "ok",
            f"{resp.status_code}, kb_version={body.get('kb_version')}, kb_products={body.get('kb_products')}")

    def wrong_key():
        resp = scan(KNOWN_SCAN_TEXT, {**headers, "X-API-Key": WRONG_KEY})
        add("wrong key", resp.status_code == 401, f"{resp.status_code} (want 401)")

    def empty_text():
        resp = scan("   ", headers)
        body = _json(resp)
        add("empty text", resp.status_code == 200 and body.get("status") == "rescan"
            and body.get("rescan_reason") == "no_text",
            f"{resp.status_code}, status={body.get('status')}, rescan_reason={body.get('rescan_reason')}")

    def known_scan():
        resp = scan(KNOWN_SCAN_TEXT, headers)
        body = _json(resp)
        add("known scan", resp.status_code == 200 and body.get("status") in ("ok", "ok_caveat")
            and bool(body.get("spoken_text")),
            f"{resp.status_code}, status={body.get('status')}, tier={body.get('tier')}, "
            f"cache={body.get('cache')}, request_id={body.get('request_id') or resp.headers.get('X-Request-Id')}")

    def docs_off():
        codes = {path: client.get(f"{base_url}{path}", **kw).status_code
                 for path in ("/docs", "/openapi.json")}
        add("docs off", all(code == 404 for code in codes.values()),
            ", ".join(f"{path} {code}" for path, code in codes.items()) + " (want 404)")

    def rate_limit():
        for sent in range(1, MAX_RATE_LIMIT_TRIES + 1):
            resp = scan("   ", headers)
            if resp.status_code == 429:
                body = _json(resp)
                add("rate limit", bool(resp.headers.get("Retry-After")) and bool(body.get("spoken_text")),
                    f"429 after {sent} more scans, reason={body.get('reason')}, "
                    f"Retry-After={resp.headers.get('Retry-After')}")
                return
            if resp.status_code != 200:
                add("rate limit", False, f"unexpected {resp.status_code} after {sent} scans")
                return
        add("rate limit", False, f"no 429 after {MAX_RATE_LIMIT_TRIES} scans")

    for name, step in (("health", health), ("wrong key", wrong_key), ("empty text", empty_text),
                       ("known scan", known_scan), ("docs off", docs_off), ("rate limit", rate_limit)):
        guarded(name, step)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a deployed MedSight backend.")
    parser.add_argument("--url", required=True, help="the service's base URL, e.g. https://<staging host>")
    args = parser.parse_args()
    api_key = os.environ.get("SMOKE_API_KEY", "")
    if not api_key:
        print("Set SMOKE_API_KEY to an app key for this environment.", file=sys.stderr)
        return 1

    with requests.Session() as session:
        checks = run(session, args.url, api_key)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name:<11} {c.detail}")
    failed = [c.name for c in checks if not c.ok]
    print(f"{len(checks) - len(failed)}/{len(checks)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
