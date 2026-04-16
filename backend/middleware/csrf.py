"""CSRF double-submit token.

On session creation we issue a signed CSRF token in a readable (non-HttpOnly) cookie
and expect the same value echoed in the X-XSRF-TOKEN header on mutations.

GET / HEAD / OPTIONS are exempt. The middleware emits a proper typed JSON error
on violations — it does NOT raise, because ASGI middleware exceptions don't go
through FastAPI's handler chain (they'd become 500).
"""
from __future__ import annotations

import hmac
import json
import secrets
from hashlib import sha256

from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings
from ..errors import CsrfInvalid

CSRF_COOKIE = "XSRF-TOKEN"
CSRF_HEADER = "x-xsrf-token"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def sign(token: str) -> str:
    settings = get_settings()
    mac = hmac.new(settings.CSRF_SIGNING_KEY.encode(), token.encode(), sha256).hexdigest()
    return f"{token}.{mac}"


def verify(signed: str) -> bool:
    try:
        raw, mac = signed.rsplit(".", 1)
    except ValueError:
        return False
    settings = get_settings()
    expected = hmac.new(settings.CSRF_SIGNING_KEY.encode(), raw.encode(), sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS = {
    "/api/auth/csrf",   # issues the token
    "/api/auth/login",  # login itself can't have a prior session's CSRF
    "/api/health",
    "/api/health/ready",
    "/metrics",
}


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: PLR0912
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]

        if method in SAFE_METHODS or path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        header_token = None
        cookie_token = None
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                for raw_part in v.decode(errors="replace").split(";"):
                    part = raw_part.strip()
                    if part.startswith(f"{CSRF_COOKIE}="):
                        cookie_token = part.split("=", 1)[1]
                        break
            elif k.decode().lower() == CSRF_HEADER:
                header_token = v.decode()

        err: CsrfInvalid | None = None
        if not header_token or not cookie_token:
            err = CsrfInvalid("CSRF token missing.")
        elif header_token != cookie_token:
            err = CsrfInvalid("CSRF token mismatch.")
        elif not verify(cookie_token):
            err = CsrfInvalid("CSRF token signature invalid.")

        if err is not None:
            # Best-effort: find request_id from headers if present.
            rid = None
            for k, v in scope.get("headers", []):
                if k.decode().lower() == "x-request-id":
                    rid = v.decode()
                    break
            body = json.dumps(err.to_response(rid)).encode()
            await send({
                "type": "http.response.start",
                "status": err.http_status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
