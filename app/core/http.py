"""Small ASGI hardening middleware without changing API business behavior."""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.responses import JSONResponse

ASGIMessage = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class RequestBodyTooLargeError(Exception):
    """Raised internally when a streamed HTTP body exceeds the configured limit."""


class RequestBodyLimitMiddleware:
    """Reject oversized Content-Length and chunked request bodies with HTTP 413."""

    def __init__(self, app: Any, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(
        self, scope: dict[str, Any], receive: Receive, send: Send
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > self.max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> ASGIMessage:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLargeError:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: dict[str, Any], receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": "Request body exceeds the configured limit"},
            status_code=413,
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Add browser-safe headers while leaving Swagger UI functionality intact."""

    _HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    )

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self, scope: dict[str, Any], receive: Receive, send: Send
    ) -> None:
        async def send_with_headers(message: ASGIMessage) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                headers.extend(
                    (key, value) for key, value in self._HEADERS if key not in existing
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
