"""
Structured logs: one JSON object per line on stdout, so the host (Railway)
can keep and search them. Every line written while a request is being handled
carries that request's ID.

Usage:
    log = logging.getLogger(__name__)
    log.info("scan done", extra={"fields": {"status": "ok", "latency_ms": 812}})

Never log API keys, headers or full request bodies.
"""
import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from starlette.datastructures import MutableHeaders

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

# A client-sent request ID is only reused if it's short and plain, so it
# can't inject fake fields or lines into the logs.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

log = logging.getLogger("medsight.http")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        # ASCII only: a non-UTF-8 stdout must never fail a request.
        return json.dumps(entry, ensure_ascii=True, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send all logs (ours and uvicorn's) to stdout as JSON. Safe to call twice."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # RequestIdMiddleware logs each request with its ID, so uvicorn's own
    # access line would only be a duplicate without one.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


def new_request_id(incoming: Optional[str]) -> str:
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestIdMiddleware:
    """Gives every HTTP request an ID (the client's X-Request-Id if it's
    valid, else a new one), returns it in the X-Request-Id response header,
    and logs one line per request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = new_request_id(incoming)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-Id", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            log.info("request", extra={"fields": {
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status_code": status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }})
            request_id_var.reset(token)
