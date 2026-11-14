"""
The review console's API as its own deployed service (a second Railway service
from the same image), for the hosted review page on Vercel.

    uvicorn app.review_service:app --host 0.0.0.0 --port $PORT

It serves only the review routes (/admin/kb/..., /admin/review/stats,
/admin/web-answers...) and /health: no scan API, no answer-cache admin, no API
docs. The page is served by Vercel (backend/review/), which is why CORS is
needed here and only for REVIEW_ALLOWED_ORIGINS.

It refuses to start unless:
- REVIEW_DATABASE_URL, SUPABASE_URL and SUPABASE_ANON_KEY are set, and
- REVIEW_ALLOWED_ORIGINS names the hosted page (https only).

Two-step sign-in follows REVIEW_REQUIRE_MFA (default on). Off is allowed for a
demo, but logged as a warning at startup and shown as "two_step": "off" on /health.
The page's own setting (REVIEW_REQUIRE_MFA in the Vercel build) must match.
"""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import config, review
from app.api import BodySizeLimitMiddleware
from app.logging_setup import RequestIdMiddleware, configure_logging

log = logging.getLogger(__name__)

REQUIRED_SETTINGS = ("REVIEW_DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY")


class NotReady(RuntimeError):
    """A setting the hosted review service can't run without is missing or unsafe."""


def check_settings() -> None:
    missing = [name for name in REQUIRED_SETTINGS if not getattr(config, name)]
    if missing:
        raise NotReady(f"Set {', '.join(missing)} for the review service.")
    if not config.REVIEW_ALLOWED_ORIGINS:
        raise NotReady("Set REVIEW_ALLOWED_ORIGINS to the hosted review page, e.g. https://medsight-review.vercel.app")
    if any(not o.startswith("https://") or o.endswith("/") for o in config.REVIEW_ALLOWED_ORIGINS):
        raise NotReady("REVIEW_ALLOWED_ORIGINS must be https:// origins with no trailing slash.")
    if not config.REVIEW_REQUIRE_MFA:
        # Allowed (Mon, 2026-11-14, for the demo), but never quietly: a public
        # sign-in page with a password alone is one leaked password from an approval.
        log.warning("two-step sign-in is OFF for the hosted review service; set REVIEW_REQUIRE_MFA=on "
                    "(and storage variant (b) in kb/README.md) once the demo is over")


def build_app() -> FastAPI:
    check_settings()
    config.ADMIN_ENABLED = True   # this process serves only the review routes below
    app = FastAPI(title="MedSight review service", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=config.REVIEW_ALLOWED_ORIGINS,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])
    app.add_middleware(RequestIdMiddleware)   # outermost, so every response has an ID

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "review", "two_step": "on" if config.REVIEW_REQUIRE_MFA else "off",
                "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7] or None}

    app.include_router(review.router)
    return app


def _create() -> FastAPI:
    configure_logging(config.LOG_LEVEL)
    try:
        built = build_app()
    except NotReady as e:
        log.error("review service not started", extra={"fields": {"reason": str(e)}})
        raise
    log.info("review service started", extra={"fields": {"origins": config.REVIEW_ALLOWED_ORIGINS}})
    return built


def __getattr__(name: str):
    # "app" is built on first use (uvicorn's import), so importing this module
    # in tests doesn't need the settings.
    if name == "app":
        globals()["app"] = built = _create()
        return built
    raise AttributeError(name)
