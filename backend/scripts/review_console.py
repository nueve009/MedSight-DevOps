"""
The review console on your own laptop: the page and its API from one local
address. Approvals land in Supabase, and the deployed backend serves them
within KB_REFRESH_S (60 seconds), so updating the dataset never needs a deploy.

Usage (from backend/):
    python -m scripts.review_console
then open http://127.0.0.1:8100/review/

Two-step sign-in (an authenticator code) is off here unless you pass
--require-mfa. Turn it on before the console is hosted (docs/runbook.md).

It reads REVIEW_DATABASE_URL, SUPABASE_URL and SUPABASE_ANON_KEY from .env
(setup: kb/README.md, "Reviewing in the console"). It serves only the review
console, not the scan API, and listens on this computer only unless --host is
given on the command line.
"""
import argparse
import ipaddress
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import config, review
from app.api import BodySizeLimitMiddleware
from app.logging_setup import RequestIdMiddleware, configure_logging

PAGE_DIR = Path(__file__).resolve().parent.parent / "review"
LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8100
REQUIRED_SETTINGS = ("REVIEW_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY")

log = logging.getLogger(__name__)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def choose_host(from_flag: Optional[str], from_env: Optional[str]) -> str:
    """Where to listen. Only an explicit --host may leave this computer; a setting in the environment can't."""
    if from_flag:
        return from_flag
    if from_env:
        if not is_loopback(from_env):
            raise SystemExit(f"Refusing to listen on {from_env}: use --host on the command line if you really mean it.")
        return from_env
    return LOOPBACK


def missing_settings() -> list[str]:
    return [name for name in REQUIRED_SETTINGS if not getattr(config, name)]


def content_security_policy() -> str:
    supabase = config.SUPABASE_URL
    return (f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' {supabase}; "
            f"img-src 'self' data: {supabase}; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def page_config() -> str:
    values = {"supabaseUrl": config.SUPABASE_URL, "supabaseAnonKey": config.SUPABASE_ANON_KEY, "apiBase": "",
              "requireMfa": config.REVIEW_REQUIRE_MFA}
    return f"window.REVIEW_CONFIG = {json.dumps(values)};\n"


def build_app(allowed_hosts: Optional[list[str]] = None) -> FastAPI:
    """Only the review console: no scan routes, no answer-cache admin, no API docs."""
    config.ADMIN_ENABLED = True   # this process only; the deployed backend has its own setting
    app = FastAPI(title="MedSight review console", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = content_security_policy()
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/review/")

    @app.get("/review/config.js", include_in_schema=False)
    def config_js() -> Response:
        return Response(page_config(), media_type="text/javascript", headers={"Cache-Control": "no-store"})

    app.include_router(review.router)
    app.mount("/review", StaticFiles(directory=PAGE_DIR, html=True), name="review")
    return app


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the review console on this computer.")
    parser.add_argument("--host", help="listen on another address (this computer only, if left out)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--require-mfa", action="store_true", help="also require an authenticator code at sign-in")
    return parser.parse_args(argv)


def mfa_line(require_mfa: bool) -> str:
    return ("Two-step sign-in: ON" if require_mfa
            else "Two-step sign-in: OFF (local testing only; turn on before hosting)")


def main() -> int:
    args = parse_args()
    config.REVIEW_REQUIRE_MFA = args.require_mfa   # this process only
    host = choose_host(args.host, os.environ.get("REVIEW_CONSOLE_HOST"))

    missing = missing_settings()
    if missing:
        print(f"Set these in backend/.env first: {', '.join(missing)} (see kb/README.md).")
        return 1
    configure_logging(config.LOG_LEVEL)
    if not is_loopback(host):
        log.warning("review console listening beyond this computer", extra={"fields": {"host": host}})
        print(f"WARNING: listening on {host}. Anyone who can reach it sees the sign-in page.")
    allowed = ["*"] if host == "0.0.0.0" else ["127.0.0.1", "localhost", host]   # noqa: S104
    print(f"Review console: http://{host}:{args.port}/review/  (Ctrl+C to stop)")
    print(mfa_line(args.require_mfa))
    uvicorn.run(build_app(allowed), host=host, port=args.port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
