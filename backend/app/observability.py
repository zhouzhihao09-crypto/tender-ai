"""Structured logging and request ID correlation for Phase 15B.

This module provides:

* A request ID that is generated when the client does not supply one,
  preserved when the client supplies a valid one, and replaced when the
  supplied value is missing, blank, malformed, or unreasonably long.
* A :class:`logging.Filter` that injects the request ID into every log
  record emitted while a request is being handled.
* A JSON formatter suitable for production log aggregation.
* A Starlette middleware that:

  - reads ``X-Request-ID`` (falling back to a generated UUID),
  - binds the ID to a :class:`contextvars.ContextVar` for the lifetime
    of the request,
  - returns the final ID in the ``X-Request-ID`` response header,
  - logs one line per request (method, path, status, duration),
  - logs unhandled exceptions with the request ID and re-raises them
    so existing FastAPI/Starlette error handling is unchanged.

The standard library is used exclusively; no third-party logging
dependency is introduced.  Background jobs (RQ / BackgroundTasks) are
identified by their own job ID (``analysis:<tender_id>``); they do not
inherit HTTP request IDs because no durable correlation data is
persisted.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LENGTH = 128

# Bound for the lifetime of a single HTTP request.  Log records emitted
# outside a request (startup, background jobs, tests) see ``None``.
request_id_var: ContextVar[str | None] = ContextVar("tender_ai_request_id", default=None)


def generate_request_id() -> str:
    """Return a fresh UUID-based request identifier."""
    return uuid.uuid4().hex


def normalize_request_id(value: str | None) -> str | None:
    """Return *value* if it is a usable request ID, else ``None``.

    A usable ID is non-blank, within the length limit, and free of
    characters that would break header parsing or log line alignment.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > _MAX_REQUEST_ID_LENGTH:
        return None
    # Reject control characters and whitespace that would corrupt the
    # single-line JSON log format or the response header.
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        return None
    return candidate


class RequestIdFilter(logging.Filter):
    """Inject the active request ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - matches stdlib API
        record.request_id = request_id_var.get() or "-"
        return True


class StructuredFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str | None = None) -> None:
    """Configure root logging with the structured handler and filter.

    Idempotent: calling twice does not stack duplicate handlers.
    """
    root = logging.getLogger()
    root.setLevel((level or "INFO").upper())

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter())
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)

    # Keep uvicorn's own loggers visible through the same pipeline.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        logger = logging.getLogger(name)
        logger.setLevel(root.level)
        logger.propagate = True


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate, preserve, and expose request IDs for every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = normalize_request_id(incoming) or generate_request_id()

        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            # Log the correlation point, then re-raise so existing
            # exception handling (or the default 500) is unchanged.
            logging.getLogger("tender_ai.request").exception(
                "Unhandled exception for %s %s",
                request.method,
                request.url.path,
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            logging.getLogger("tender_ai.request").info(
                "%s %s -> %s in %.2f ms",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
            )
            request_id_var.reset(token)