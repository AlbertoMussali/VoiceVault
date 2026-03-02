from __future__ import annotations

from collections import defaultdict, deque
import math
import threading
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _RequestTooLargeError(Exception):
    pass


def _extract_client_ip(headers: Headers, scope: Scope) -> str:
    forwarded_for = headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "unknown"


def _response_start_with_headers(message: Message, extra_headers: dict[str, str]) -> Message:
    updated = dict(message)
    mutable_headers = MutableHeaders(scope=updated)
    for name, value in extra_headers.items():
        mutable_headers[name] = value
    return updated


class RequestSizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_request_size_bytes: int,
        max_audio_upload_size_bytes: int,
    ) -> None:
        self.app = app
        self.max_request_size_bytes = max_request_size_bytes
        self.max_audio_upload_size_bytes = max_audio_upload_size_bytes

    def _limit_for_path(self, path: str) -> int:
        if path.startswith("/api/v1/entries/") and path.endswith("/audio"):
            return self.max_audio_upload_size_bytes
        return self.max_request_size_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        limit = self._limit_for_path(path)
        headers = Headers(raw=scope.get("headers", []))

        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > limit:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body exceeds limit of {limit} bytes."},
                )
                await response(scope, receive, send)
                return

        total_read = 0

        async def limited_receive() -> Message:
            nonlocal total_read
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_read += len(body)
                if total_read > limit:
                    raise _RequestTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestTooLargeError:
            response = JSONResponse(
                status_code=413,
                content={"detail": f"Request body exceeds limit of {limit} bytes."},
            )
            await response(scope, receive, send)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        window_seconds: int,
        api_limit: int,
        auth_limit: int,
    ) -> None:
        self.app = app
        self.window_seconds = window_seconds
        self.api_limit = api_limit
        self.auth_limit = auth_limit
        self._request_windows: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _bucket_for_path(self, path: str) -> tuple[str, int] | None:
        if not path.startswith("/api/v1/"):
            return None
        if path.startswith("/api/v1/auth/"):
            return "auth", self.auth_limit
        return "api", self.api_limit

    def _check_and_record(self, key: str, limit: int, now: float) -> tuple[bool, int, int]:
        with self._lock:
            history = self._request_windows[key]
            cutoff = now - self.window_seconds
            while history and history[0] <= cutoff:
                history.popleft()

            if len(history) >= limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - history[0])))
                return False, 0, retry_after

            history.append(now)
            remaining = max(0, limit - len(history))
            return True, remaining, self.window_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        bucket = self._bucket_for_path(path)
        if bucket is None:
            await self.app(scope, receive, send)
            return

        bucket_name, limit = bucket
        headers = Headers(raw=scope.get("headers", []))
        client_ip = _extract_client_ip(headers, scope)
        key = f"{bucket_name}:{client_ip}"
        now = time.monotonic()
        allowed, remaining, retry_after = self._check_and_record(key, limit, now)

        header_values = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(retry_after),
        }

        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Retry later."},
                headers={**header_values, "Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        async def send_with_rate_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = _response_start_with_headers(message, header_values)
            await send(message)

        await self.app(scope, receive, send_with_rate_headers)
