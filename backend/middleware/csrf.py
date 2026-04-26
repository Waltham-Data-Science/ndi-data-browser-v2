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
from ..errors import AuthRateLimited, CsrfInvalid

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
    # Issues the CSRF cookie — must be reachable without one.
    "/api/auth/csrf",
    # Health + observability — never mutating, never exempt from rate-limit.
    "/api/health",
    "/api/health/ready",
    "/metrics",
}
# Previously `/api/auth/login` was also exempted on the premise that a
# pre-session user couldn't have a CSRF token. That's wrong: the frontend's
# `ensureCsrfToken()` (see frontend/src/api/client.ts) fetches
# /api/auth/csrf to mint a token before submitting the login form, so login
# DOES have a valid token pair. Exempting it opened classic login-CSRF
# where evil.com could POST the victim's browser to /api/auth/login with
# the attacker's credentials, silently switching the victim into the
# attacker's account. Audit 2026-04-23, issue #53.


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
                        # Cookie values occasionally carry surrounding
                        # whitespace (Safari adds a space after ";") or
                        # wrap a RFC6265 "quoted-string" in double
                        # quotes. Either would produce a spurious
                        # header/cookie mismatch below → bogus 403.
                        cookie_token = (
                            part.split("=", 1)[1].strip().strip('"')
                        )
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

            # O4: per-IP CSRF-failure rate limit. Probes that spam
            # mutating endpoints with bogus tokens consume cycles and
            # bury the real attack signal in the noise. Burn one budget
            # slot per failure; on overflow, replace the 403 CSRF_INVALID
            # with a 429 AUTH_RATE_LIMITED so the client (and metrics)
            # see the right typed reason. The bucket is `csrf-fail-ip`
            # — distinct from the login buckets so dashboards can split
            # CSRF probing from login-credential probing.
            #
            # We swallow any rate-limiter error so a Redis hiccup never
            # promotes a CSRF 403 into a 500 — security checks degrade
            # to "still 403" rather than "open."
            err = await self._maybe_promote_to_rate_limit(scope, err)

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

    @staticmethod
    async def _maybe_promote_to_rate_limit(
        scope: Scope, original_err: CsrfInvalid,
    ) -> CsrfInvalid | AuthRateLimited:
        """Burn a CSRF-fail budget slot for the request's IP. If the
        budget is exhausted, return a 429 AuthRateLimited; otherwise
        return the original 403 CsrfInvalid unchanged.

        Any limiter exception (Redis down, missing app.state attr) is
        swallowed and the original 403 is returned — security checks
        must degrade to "still 403" not "open."
        """
        try:
            # Pure ASGI middleware — no Request object handy. Pull the
            # rate limiter off scope["app"].state where the lifespan
            # planted it (`backend/app.py:lifespan`). If the path goes
            # through `app.state`, the attribute is set; otherwise we
            # silently fall through.
            app = scope.get("app")
            limiter = getattr(getattr(app, "state", None), "rate_limiter", None)
            if limiter is None:
                return original_err

            # Pull the IP. ASGI scope's "client" is (host, port) or None.
            client = scope.get("client")
            ip = (
                client[0]
                if isinstance(client, (tuple, list)) and len(client) >= 1
                else "unknown"
            )

            from .rate_limit import Limit, RateLimiter

            settings = get_settings()
            await limiter.check(
                Limit(
                    bucket="csrf-fail-ip",
                    max_requests=settings.RATE_LIMIT_CSRF_FAIL_PER_IP_5MIN,
                    window_seconds=5 * 60,
                    auth_bucket=True,
                ),
                RateLimiter.subject_for(None, ip),
            )
        except AuthRateLimited as e:
            return e
        except Exception:
            # Any other failure (Redis, attr lookup, etc.) — leave the
            # original CSRF 403 in place.
            return original_err
        return original_err
