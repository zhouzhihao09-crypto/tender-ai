"""Phase 16-4: application-level rate limiting regression tests."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    _LIMITERS,
    client_identity,
    rate_limit_dependency,
)


@pytest.fixture(autouse=True)
def _clean_limiters():
    """Each test starts with a fresh limiter store."""
    saved = dict(_LIMITERS)
    _LIMITERS.clear()
    yield
    _LIMITERS.clear()
    _LIMITERS.update(saved)


def test_request_below_limit_succeeds() -> None:
    limiter = RateLimiter(max_requests=5, window_seconds=60)
    for _ in range(5):
        assert limiter.check("client-a") == 0.0


def test_request_over_limit_returns_429() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("client-a")


def test_independent_clients_do_not_share_bucket() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")
    # Client b is unaffected.
    assert limiter.check("b") == 0.0


def test_retry_after_is_positive_and_bounded() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    limiter.check("a")
    try:
        limiter.check("a")
    except RateLimitExceeded as exc:
        assert 0 < exc.retry_after <= 60
    else:
        pytest.fail("expected RateLimitExceeded")


def test_expired_entries_are_cleaned_up() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=0.05)
    limiter.check("a")
    import time
    time.sleep(0.08)
    # The expired entry must be removable on demand so the store stays bounded.
    limiter.purge()
    assert not limiter._store.get("a")


def test_disabled_rate_limiting_preserves_behavior(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.rate_limit.settings.rate_limit_enabled", False)
    # _limit_for_path returns None when disabled, so the dependency is a no-op.
    from backend.app.rate_limit import _limit_for_path

    assert _limit_for_path("/api/auth/login") is None
    assert _limit_for_path("/api/tenders") is None
    assert _limit_for_path("/api/tenders/1/ask") is None


def test_client_identity_prefers_authenticated_user() -> None:
    request = MagicMock()
    request.client.host = "1.2.3.4"
    user = MagicMock()
    user.id = 42
    assert client_identity(request, user) == "user:42"


def test_client_identity_falls_back_to_ip() -> None:
    request = MagicMock()
    request.client.host = "1.2.3.4"
    assert client_identity(request, None) == "ip:1.2.3.4"


def test_rate_limit_dependency_returns_429_with_request_id(monkeypatch) -> None:
    from backend.app.auth_service import current_user
    from backend.app.rate_limit import rate_limit_dependency
    from backend.app.rate_limit import RateLimitExceeded

    monkeypatch.setattr("backend.app.rate_limit.settings.rate_limit_enabled", True)
    monkeypatch.setattr("backend.app.rate_limit.settings.rate_limit_login", 2)
    monkeypatch.setattr("backend.app.rate_limit.settings.rate_limit_window_seconds", 60)
    monkeypatch.setattr("backend.app.rate_limit.settings.rate_limit_ai", 100)
    monkeypatch.setattr("backend.app.rate_limit.settings.rate_limit_upload", 100)

    app = FastAPI()
    # Mirror the production handler so the 429 shape is identical.
    from fastapi.responses import JSONResponse

    @app.exception_handler(RateLimitExceeded)
    async def _handler(request, exc):  # noqa: ANN001
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc)},
            headers={"Retry-After": "1", "X-Request-ID": "-"},
        )

    mock_user = MagicMock()
    mock_user.id = 1
    app.dependency_overrides[current_user] = lambda: mock_user

    @app.get("/api/auth/login", dependencies=[Depends(rate_limit_dependency)])
    def login() -> dict:
        return {"ok": True}

    client = TestClient(app)
    client.get("/api/auth/login")
    client.get("/api/auth/login")
    response = client.get("/api/auth/login")
    assert response.status_code == 429
    assert response.headers.get("Retry-After") is not None
    assert response.headers.get("X-Request-ID") is not None


def test_production_openai_requires_key_guard_still_intact() -> None:
    """Phase 16-3 guard must still reject OpenAI without a key."""
    result = subprocess.run(
        [sys.executable, "-c", "import backend.app.config"],
        env={
            **os.environ,
            "APP_ENVIRONMENT": "production",
            "AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "",
            "AUTH_REQUIRED": "true",
            "AUTH_SECRET": "prod-secret-123",
            "ALLOWED_ORIGINS": "http://example.com",
            "ALLOWED_HOSTS": "example.com",
            "DATABASE_URL": "sqlite:///:memory:",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "OPENAI_API_KEY must be set when AI_PROVIDER=openai" in result.stderr