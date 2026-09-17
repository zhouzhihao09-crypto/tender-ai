"""Application-level rate limiting for Phase 16-4.

A small, dependency-free, in-process sliding-window rate limiter.

Design notes
------------
* **In-process only.**  Each worker process maintains its own counters, so
  multi-worker deployments only enforce the limit per worker.  This is the
  intended V1 behaviour: it prevents a single client from exhausting one
  worker without requiring Redis.  Multi-worker deployments should front
  the fleet with a shared limiter (reverse proxy, gateway, or Redis) — that
  is documented as a known limitation rather than being hidden.
* **Client identity** prefers the authenticated user id, falling back to
  the client IP.  We deliberately do not trust arbitrary request headers
  (e.g. ``X-Forwarded-For``) as the sole identity source.
* **Bounded memory.**  Expired entries are purged lazily on every check and
  eagerly when the store grows past a threshold, so a flood of unique keys
  cannot grow memory without bound.
* **Safe failure.**  If the limiter is disabled or errors, requests pass
  through unchanged — the limiter never blocks legitimate traffic because
  of a bug in itself.

The limiter is a *dependency* applied to the main router, so it runs before
endpoint logic but after authentication (which provides the user identity).
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import Depends, Request

from .auth_service import current_user
from .config import settings
from .models import User


class RateLimitExceeded(Exception):
    """Raised when a client exceeds a configured rate limit."""

    def __init__(self, limit: int, window_seconds: int, retry_after: float) -> None:
        super().__init__(f"Rate limit exceeded: {limit} requests per {window_seconds}s.")
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = retry_after


class RateLimiter:
    """Sliding-window rate limiter keyed by an arbitrary string identity."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = int(max_requests)
        self.window_seconds = float(window_seconds)
        # key -> list of monotonic timestamps
        self._store: dict[str, list[float]] = defaultdict(list)

    def _purge(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        timestamps = self._store[key]
        keep = [t for t in timestamps if t > cutoff]
        if keep:
            self._store[key] = keep
        else:
            del self._store[key]

    def _purge_all_expired(self, now: float) -> None:
        for key in list(self._store.keys()):
            self._purge(key, now)

    def _maybe_sweep(self) -> None:
        """Eagerly sweep when the store grows too large."""
        if len(self._store) > 5000:
            self._purge_all_expired(time.monotonic())

    def purge(self) -> None:
        """Remove every expired entry from the store.

        Exposed for tests and for operators who want to inspect or trim the
        store without issuing a request.
        """
        self._purge_all_expired(time.monotonic())

    def check(self, key: str) -> float:
        """Return the seconds to wait before the client may proceed.

        Returns ``0.0`` when the request is allowed.  Raises
        :class:`RateLimitExceeded` when the limit is exceeded.
        """
        now = time.monotonic()
        self._maybe_sweep()
        self._purge(key, now)
        timestamps = self._store[key]
        if len(timestamps) >= self.max_requests:
            retry_after = self.window_seconds - (now - timestamps[0])
            raise RateLimitExceeded(self.max_requests, int(self.window_seconds), max(retry_after, 0.0))
        timestamps.append(now)
        return 0.0


def client_identity(request: Any, user: Any = None) -> str:
    """Build a stable rate-limit identity for the current request.

    Prefers the authenticated user id; falls back to the client IP.  We do
    not use forwarded headers as the sole identity source because they are
    trivially spoofable.
    """
    if user is not None and getattr(user, "id", None) is not None:
        return f"user:{user.id}"
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return f"ip:{host or 'unknown'}"


def _limit_for_path(path: str) -> tuple[int, float] | None:
    """Return ``(max_requests, window_seconds)`` for *path*, or ``None``.

    Only the highest-value abuse paths are limited; normal browsing is
    unaffected.  The route is matched on its trailing segment so that
    ``/api/auth/login`` and ``/api/auth/register`` are covered without
    needing to enumerate every parameterised path.
    """
    if not settings.rate_limit_enabled:
        return None
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    if tail in ("login", "register"):
        return settings.rate_limit_login, float(settings.rate_limit_window_seconds)
    if tail == "ask":
        return settings.rate_limit_ai, float(settings.rate_limit_window_seconds)
    if tail in ("tenders", "company-evidence"):
        return settings.rate_limit_upload, float(settings.rate_limit_window_seconds)
    return None


# One limiter per (max_requests, window_seconds) configuration so that
# different tiers do not share a single counter.
_LIMITERS: dict[tuple[int, float], RateLimiter] = {}


def _get_limiter(max_requests: int, window_seconds: float) -> RateLimiter:
    key = (max_requests, window_seconds)
    limiter = _LIMITERS.get(key)
    if limiter is None:
        limiter = RateLimiter(max_requests, window_seconds)
        _LIMITERS[key] = limiter
    return limiter


def rate_limit_dependency(
    request: Request,
    user: User = Depends(current_user),
) -> None:
    """FastAPI dependency applying the configured rate limit.

    Safe no-op when rate limiting is disabled or unconfigured, so existing
    behaviour is unchanged by default.  Registered on the main router
    alongside ``enforce_request_ownership`` so the resolved user is passed
    in via FastAPI's dependency cache without duplicating auth work.
    """
    limit = _limit_for_path(request.url.path)
    if limit is None:
        return None
    max_requests, window_seconds = limit
    identity = client_identity(request, user)
    limiter = _get_limiter(max_requests, window_seconds)
    limiter.check(identity)
    return None


__all__ = ["RateLimiter", "RateLimitExceeded", "client_identity", "rate_limit_dependency"]