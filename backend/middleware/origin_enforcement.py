"""Origin enforcement middleware (O5).

Server-side defense-in-depth for cross-origin attacks: every mutating
request must carry an Origin (or Referer) whose origin component
matches the configured CORS_ORIGINS allowlist. The browser-enforced
CORS layer covers compliant browsers, but a non-browser client (curl,
attacker tooling, a CSRF-bypass scenario) ignores CORS entirely. This
middleware catches those.

Strict no-Origin handling: if BOTH Origin and Referer are absent on a
mutating request, REJECT. Legitimate browser-driven mutations always
carry one or the other; absence is suspicious. The SPA at
``ndi-cloud.com`` is the only legitimate caller and always sends Origin.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings
from ..errors import Forbidden
from .csrf import EXEMPT_PATHS, SAFE_METHODS

# Reuse CSRF's exempt-path set: same paths need to be reachable from
# pre-session contexts. If origin enforcement diverges from CSRF in
# the future (e.g. an endpoint that needs origin-but-not-csrf), give
# this middleware its own EXEMPT set.


class OriginEnforcementMiddleware:
    """ASGI middleware that rejects mutating requests with a missing
    or non-allowlisted Origin (with Referer fallback).

    Pure ASGI rather than Starlette ``BaseHTTPMiddleware`` so the
    rejection bypasses FastAPI's exception-handler chain (same shape
    as ``CsrfMiddleware``). The rejection writes a typed JSON envelope
    matching the rest of the error catalog.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]

        # Safe methods + exempt paths bypass — same contract as CSRF.
        if method in SAFE_METHODS or path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        origin: str | None = None
        referer: str | None = None
        request_id: str | None = None
        for k, v in scope.get("headers", []):
            kl = k.decode().lower()
            if kl == "origin":
                origin = v.decode()
            elif kl == "referer":
                referer = v.decode()
            elif kl == "x-request-id":
                request_id = v.decode()

        settings = get_settings()
        allowed = set(settings.cors_origins_list)

        # Step 1: if Origin is present, it MUST be allowlisted.
        # Step 2: if Origin is absent, fall back to Referer's origin.
        # Step 3: if neither yields an allowed origin, REJECT.
        decision_origin: str | None = None
        if origin:
            decision_origin = origin
        elif referer:
            try:
                parts = urlparse(referer)
                if parts.scheme and parts.netloc:
                    decision_origin = f"{parts.scheme}://{parts.netloc}"
            except ValueError:
                decision_origin = None

        if not decision_origin or decision_origin not in allowed:
            err = Forbidden(
                "Request origin is not allowed.",
                details={
                    "have_origin": bool(origin),
                    "have_referer": bool(referer),
                },
            )
            body = json.dumps(err.to_response(request_id)).encode()
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
