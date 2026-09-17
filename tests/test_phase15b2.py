"""Phase 15B-2: Structured logging and request ID correlation.

Covers the request ID contract and the structured logging plumbing
without depending on exact log formatting.  The existing HTTP,
auth, ownership, and error-response behaviour is preserved.
"""

import logging
import re

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.observability import (
    REQUEST_ID_HEADER,
    RequestIdFilter,
    RequestIDMiddleware,
    StructuredFormatter,
    generate_request_id,
    normalize_request_id,
    request_id_var,
    setup_logging,
)

client = TestClient(app)

_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# Request ID generation and normalisation
# ---------------------------------------------------------------------------


def test_generated_request_id_is_uuid_hex() -> None:
    value = generate_request_id()
    assert _REQUEST_ID_RE.match(value)


def test_normalize_rejects_blank_and_whitespace() -> None:
    assert normalize_request_id(None) is None
    assert normalize_request_id("") is None
    assert normalize_request_id("   ") is None


def test_normalize_rejects_overly_long_id() -> None:
    assert normalize_request_id("a" * 129) is None
    assert normalize_request_id("a" * 128) is not None


def test_normalize_rejects_embedded_control_characters() -> None:
    assert normalize_request_id("a\tb") is None
    assert normalize_request_id("ab\ncd") is None


def test_normalize_preserves_valid_id() -> None:
    assert normalize_request_id("  trace-123  ") == "trace-123"


# ---------------------------------------------------------------------------
# HTTP-level request ID behaviour
# ---------------------------------------------------------------------------


def test_request_without_id_receives_generated_id() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    returned = response.headers.get(REQUEST_ID_HEADER)
    assert returned is not None
    assert _REQUEST_ID_RE.match(returned)


def test_valid_incoming_request_id_is_preserved() -> None:
    incoming = "trace-abc-123"
    response = client.get("/health", headers={REQUEST_ID_HEADER: incoming})
    assert response.headers[REQUEST_ID_HEADER] == incoming


def test_overly_long_incoming_id_is_replaced() -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "x" * 200})
    assert response.headers.get(REQUEST_ID_HEADER) is not None
    assert response.headers[REQUEST_ID_HEADER] != "x" * 200
    assert _REQUEST_ID_RE.match(response.headers[REQUEST_ID_HEADER])


def test_blank_incoming_id_is_replaced() -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "   "})
    assert _REQUEST_ID_RE.match(response.headers[REQUEST_ID_HEADER])


def test_request_id_header_present_on_error_responses() -> None:
    response = client.get("/api/tenders/999999")
    assert response.status_code == 404
    assert response.headers.get(REQUEST_ID_HEADER) is not None


# ---------------------------------------------------------------------------
# Request ID available to logging during request processing
# ---------------------------------------------------------------------------


def test_request_id_is_available_to_logging_during_request() -> None:
    """The request ID ContextVar is bound for the whole request, so any
    logging handler (or application code) can read it via
    ``request_id_var.get()``.  Verified on a standalone app so the shared
    ``TestClient(app)`` is not mutated by the probe.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as _TestClient
    from starlette.middleware.base import BaseHTTPMiddleware

    observed: list[str | None] = []

    class _Probe(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[override]
            observed.append(request_id_var.get())
            return await call_next(request)

    standalone = FastAPI()
    # Add the probe first so it sits inside the request-ID middleware and
    # can observe the bound ContextVar while the request is in flight.
    standalone.add_middleware(_Probe)
    standalone.add_middleware(RequestIDMiddleware)

    @standalone.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    incoming = "log-correlation-1"
    response = _TestClient(standalone).get("/health", headers={REQUEST_ID_HEADER: incoming})
    assert response.headers[REQUEST_ID_HEADER] == incoming
    assert observed == [incoming]


def test_log_records_outside_request_have_placeholder_id() -> None:
    assert request_id_var.get() is None
    record = logging.LogRecord(
        name="tender_ai.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------


def test_setup_logging_emits_json_with_request_id() -> None:
    """The configured handler formats records as single-line JSON."""
    setup_logging("INFO")
    root = logging.getLogger()
    formatter = next(
        (handler.formatter for handler in root.handlers
         if isinstance(handler.formatter, StructuredFormatter)),
        None,
    )
    assert formatter is not None, "expected a handler with the StructuredFormatter"

    record = logging.LogRecord(
        name="tender_ai.test_json",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    RequestIdFilter().filter(record)
    rendered = formatter.format(record)

    # Single-line JSON carrying the required fields.
    assert "\n" not in rendered
    assert '"message": "hello world"' in rendered
    assert '"request_id"' in rendered
    assert '"logger": "tender_ai.test_json"' in rendered
    assert '"level": "INFO"' in rendered
    assert '"timestamp"' in rendered
    assert "password" not in rendered


# ---------------------------------------------------------------------------
# Existing behaviour preserved
# ---------------------------------------------------------------------------


def test_existing_error_responses_unchanged() -> None:
    # Invalid tender id still 404 with the same body shape.
    response = client.get("/api/tenders/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Tender not found."}


def test_health_and_frontend_responses_unchanged() -> None:
    assert client.get("/health").json() == {"status": "ok", "service": "Tender AI"}
    assert client.get("/").status_code == 200