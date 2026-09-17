"""ASGI request body-size limit (Phase 15B).

Enforces a maximum request body size at the ASGI boundary so that an
oversized request is rejected before the application buffers or
persists it.

* Requests that declare a ``Content-Length`` larger than the limit are
  rejected immediately with HTTP 413, without a single byte of the body
  being read.
* Requests without a usable ``Content-Length`` (chunked transfer
  encoding, or no length at all) are streamed through a counting
  ``receive`` wrapper.  Once the accumulated byte count exceeds the
  limit a :class:`PayloadTooLarge` error is raised, which FastAPI's
  existing exception handling turns into a 413 JSON response.  Bytes
  beyond the limit are discarded, not buffered, so memory use stays
  bounded regardless of how large the client sends.

The limit is configured via ``max_upload_size_mb`` (default 25 MB),
the same setting already used by the per-endpoint upload checks, so
there is a single source of truth.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .observability import request_id_var

logger = logging.getLogger("tender_ai.request")

_BYTES_PER_MB = 1024 * 1024


class PayloadTooLarge(HTTPException):
    """Raised when a request body exceeds the configured size limit."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(
            status_code=413,
            detail=f"Request body exceeds the maximum allowed size of {max_bytes // _BYTES_PER_MB} MB.",
        )


class _CountingReceive:
    """Wrap a ``receive`` callable, counting bytes without buffering.

    Once the limit is exceeded, subsequent calls raise
    :class:`PayloadTooLarge`.  Any bytes beyond the limit are discarded
    rather than accumulated, so the middleware never holds an oversized
    body in memory.
    """

    def __init__(self, receive: Receive, max_bytes: int) -> None:
        self._receive = receive
        self._max_bytes = max_bytes
        self._count = 0
        self._exceeded = False

    async def __call__(self) -> Message:
        if self._exceeded:
            # Drain any remaining body chunks so the connection can be
            # reused cleanly; we never return them to the application.
            message = await self._receive()
            while message.get("more_body", False):
                message = await self._receive()
            raise PayloadTooLarge(self._max_bytes)

        message = await self._receive()
        body = message.get("body") or b""
        if body:
            self._count += len(body)
            if self._count > self._max_bytes:
                self._exceeded = True
                if message.get("more_body", False):
                    await self._drain()
                raise PayloadTooLarge(self._max_bytes)
        return message

    async def _drain(self) -> None:
        message = await self._receive()
        while message.get("more_body", False):
            message = await self._receive()


class BodySizeLimitMiddleware:
    """ASGI middleware enforcing a maximum request body size."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope.get("headers") or [])

        # Fast path: reject oversized Content-Length bodies without
        # reading a single byte of the request body.
        if content_length is not None and content_length > self.max_bytes:
            await self._send_payload_too_large(send)
            return

        # Slow path: no usable Content-Length (chunked or absent).  Count
        # bytes as they stream in and raise once the limit is crossed.
        # The inner application never receives the oversized payload.
        counting_receive = _CountingReceive(receive, self.max_bytes)
        await self.app(scope, counting_receive, send)

    @staticmethod
    def _content_length(headers: list[tuple[bytes, bytes]]) -> int | None:
        for name, value in headers:
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    async def _send_payload_too_large(self, send: Send) -> None:
        request_id = request_id_var.get() or "-"
        payload = json.dumps(
            {"detail": f"Request body exceeds the maximum allowed size of {self.max_bytes // _BYTES_PER_MB} MB."}
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"x-request-id", request_id.encode()),
            ],
        })
        await send({"type": "http.response.body", "body": payload, "more_body": False})


def build_body_size_limit_middleware(max_bytes: int) -> Any:
    """Return a middleware class configured with *max_bytes*.

    Starlette's ``add_middleware`` accepts either a class or a partial;
    this helper returns a callable class so the limit is bound at app
    startup rather than on every request.
    """

    class _BoundBodySizeLimitMiddleware(BodySizeLimitMiddleware):
        def __init__(self, app: ASGIApp) -> None:  # noqa: D401 - Starlette protocol
            super().__init__(app, max_bytes)

    return _BoundBodySizeLimitMiddleware