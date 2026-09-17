"""Phase 15B-4: ASGI request body-size limit regression tests."""

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.body_limit import BodySizeLimitMiddleware, build_body_size_limit_middleware
from backend.app.config import settings
from backend.app.main import app

client = TestClient(app)

_LIMIT_BYTES = settings.max_upload_size_mb * 1024 * 1024
_OVER = b"x" * (settings.max_upload_size_mb * 1024 * 1024 + 1)


def _small_app(max_bytes: int) -> TestClient:
    """Build a minimal app with a small body limit for boundary tests."""
    inner = FastAPI()

    @inner.post("/echo")
    def echo() -> dict:
        return {"ok": True}

    return TestClient(build_body_size_limit_middleware(max_bytes)(inner))


# ---------------------------------------------------------------------------
# Below / at / above the limit
# ---------------------------------------------------------------------------


def test_request_below_limit_succeeds() -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_body_at_exact_limit_is_accepted() -> None:
    small = _small_app(64)
    response = small.post("/echo", content=b"x" * 64)
    assert response.status_code == 200


def test_body_one_byte_over_limit_is_rejected() -> None:
    small = _small_app(64)
    response = small.post("/echo", content=b"x" * 65)
    assert response.status_code == 413


def test_tender_upload_below_limit_succeeds() -> None:
    from tests.test_pdf_service import make_text_pdf

    pages = ["Tender issue date: 01 September 2026. Submission deadline: 30 September 2026 at 5:30 PM."]
    response = client.post(
        "/api/tenders",
        files={"file": ("small.pdf", make_text_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 201


def test_oversized_post_with_content_length_returns_413() -> None:
    response = client.post("/health", content=b"x" * (_LIMIT_BYTES + 1))
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"].lower()


def test_oversized_multipart_returns_413() -> None:
    response = client.post(
        "/api/tenders",
        files={"file": ("big.pdf", io.BytesIO(_OVER), "application/pdf")},
    )
    assert response.status_code == 413


def test_oversized_evidence_upload_returns_413() -> None:
    response = client.post(
        "/api/company-evidence",
        files={"file": ("big.pdf", io.BytesIO(_OVER), "application/pdf")},
    )
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# Request ID behaviour on rejected requests
# ---------------------------------------------------------------------------


def test_oversized_request_still_receives_request_id() -> None:
    response = client.post("/health", content=b"x" * (_LIMIT_BYTES + 1))
    assert response.status_code == 413
    assert response.headers.get("X-Request-ID") is not None


def test_client_supplied_request_id_preserved_on_413() -> None:
    incoming = "trace-413-1"
    response = client.post(
        "/health",
        content=b"x" * (_LIMIT_BYTES + 1),
        headers={"X-Request-ID": incoming},
    )
    assert response.status_code == 413
    assert response.headers["X-Request-ID"] == incoming


# ---------------------------------------------------------------------------
# Existing error-response conventions remain intact
# ---------------------------------------------------------------------------


def test_existing_404_response_unchanged() -> None:
    response = client.get("/api/tenders/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Tender not found."}


def test_existing_400_response_unchanged() -> None:
    from tests.test_pdf_service import make_text_pdf

    response = client.post(
        "/api/company-evidence",
        files={"file": ("bad.txt", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Only PDF" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Authentication / ownership behaviour unchanged
# ---------------------------------------------------------------------------


def test_unauthenticated_request_not_rejected_by_body_limit() -> None:
    """In development mode (AUTH_REQUIRED=false) unauthenticated requests
    are permitted; the body limit must not interfere with auth behaviour."""
    response = client.get("/api/tenders")
    assert response.status_code != 413


def test_oversized_request_does_not_bypass_auth(monkeypatch) -> None:
    """When AUTH_REQUIRED is enabled, an oversized request must still be
    rejected by authentication rather than accepted by accident."""
    monkeypatch.setattr("backend.app.auth_service.settings.auth_required", True)
    response = client.post(
        "/api/tenders",
        files={"file": ("big.pdf", io.BytesIO(_OVER), "application/pdf")},
    )
    assert response.status_code in (401, 403, 413)


# ---------------------------------------------------------------------------
# Chunked / no-Content-Length bodies cannot bypass the limit
# ---------------------------------------------------------------------------


def test_chunked_body_below_limit_succeeds() -> None:
    """A chunked body within the limit must be delivered to the app."""
    import asyncio

    from backend.app.body_limit import BodySizeLimitMiddleware

    observed: list[int] = []

    async def inner_app(scope, receive, send):
        total = 0
        while True:
            message = await receive()
            total += len(message.get("body") or b"")
            if not message.get("more_body", False):
                break
        observed.append(total)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    chunks = [
        {"type": "http.request", "body": b"a" * 20, "more_body": True},
        {"type": "http.request", "body": b"b" * 20, "more_body": False},
    ]

    async def receive():
        if chunks:
            return chunks.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = BodySizeLimitMiddleware(inner_app, max_bytes=64)
    asyncio.run(middleware({"type": "http", "headers": []}, receive, send))
    assert observed == [40]


def test_chunked_body_without_content_length_is_limited() -> None:
    """A request that streams its body in pieces without declaring a
    Content-Length must still be rejected once the limit is exceeded.
    The inner application must never observe the full body; the
    PayloadTooLarge error propagates so FastAPI's exception handling
    renders a 413 response."""
    import asyncio

    from backend.app.body_limit import BodySizeLimitMiddleware, PayloadTooLarge

    observed: list[int] = []
    chunks = [
        {"type": "http.request", "body": b"a" * 30, "more_body": True},
        {"type": "http.request", "body": b"b" * 30, "more_body": True},
        {"type": "http.request", "body": b"c" * 30, "more_body": False},
    ]

    async def inner_app(scope, receive, send):
        total = 0
        while True:
            message = await receive()
            total += len(message.get("body") or b"")
            if not message.get("more_body", False):
                break
        observed.append(total)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def receive():
        if chunks:
            return chunks.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = BodySizeLimitMiddleware(inner_app, max_bytes=64)
    with pytest.raises(PayloadTooLarge):
        asyncio.run(middleware({"type": "http", "headers": []}, receive, send))

    # The inner app must never have observed the full body.
    assert observed == []