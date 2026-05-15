"""Login / logout orchestration.

Called from the auth router. Handles rate limiting, cloud auth, session creation,
and cookie issuance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response

from ..clients.ndi_cloud import NdiCloudClient
from ..config import get_settings
from ..middleware.csrf import CSRF_COOKIE, generate_token, sign
from ..middleware.rate_limit import Limit, RateLimiter
from ..observability.logging import get_logger
from ..observability.metrics import login_attempts_total
from .cookie_attrs import cookie_attrs
from .dependencies import SESSION_COOKIE
from .session import SessionData, SessionStore

log = get_logger(__name__)


@dataclass(slots=True)
class LoginResult:
    session: SessionData
    csrf_token: str


async def do_login(
    *,
    request: Request,
    response: Response,
    username: str,
    password: str,
    store: SessionStore,
    cloud: NdiCloudClient,
    limiter: RateLimiter,
) -> LoginResult:
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"

    # Rate limits.
    await limiter.check(
        Limit(
            bucket="login-ip",
            max_requests=settings.RATE_LIMIT_LOGIN_PER_IP_15MIN,
            window_seconds=15 * 60,
            auth_bucket=True,
        ),
        RateLimiter.subject_for(None, ip),
    )
    await limiter.check(
        Limit(
            bucket="login-user",
            max_requests=settings.RATE_LIMIT_LOGIN_PER_USER_HOUR,
            window_seconds=60 * 60,
            auth_bucket=True,
        ),
        RateLimiter.subject_for(username.lower(), ip),
    )

    try:
        auth = await cloud.login(email=username, password=password)
    except Exception as e:
        login_attempts_total.labels(outcome="failure").inc()
        log.info("auth.login.failed", reason=type(e).__name__)
        raise

    # Extract org memberships + admin flag from the cloud's login
    # response (`UserWithOrganizationsResult`) and cache them on the
    # session. The cloud returns `user.organizations: [{id, name,
    # canUploadDataset}, ...]` and `user.isAdmin: bool`. We store just
    # the IDs (sufficient for fan-out queries to the cloud's
    # `/organizations/:orgId/datasets`) + the admin bit (for
    # frontend UX affordances like an admin scope toggle on /my).
    user_payload: dict[str, Any] = auth.user or {}
    raw_orgs = user_payload.get("organizations") or []
    organization_ids: list[str] = []
    if isinstance(raw_orgs, list):
        for o in raw_orgs:
            if isinstance(o, dict) and isinstance(o.get("id"), str):
                organization_ids.append(o["id"])
    is_admin = bool(user_payload.get("isAdmin", False))

    session = await store.create(
        user_id=user_payload.get("id", username) if isinstance(user_payload.get("id"), str) else username,
        email=username,
        access_token=auth.access_token,
        access_token_expires_in_seconds=auth.expires_in_seconds,
        ip=ip,
        user_agent=request.headers.get("user-agent", "unknown"),
        organization_ids=organization_ids,
        is_admin=is_admin,
    )
    login_attempts_total.labels(outcome="success").inc()
    # Log only the first 8 chars of the session id. Anyone with read
    # access to Railway logs (the whole team) could otherwise replay
    # a live session by setting the `session` cookie to a full id
    # observed in a log line — the session id IS the secret. Other
    # callsites (`session.py:202`, `session.py:214`) already
    # truncate; this success path was the holdout.
    log.info("auth.login.success", session_id=session.session_id[:8])

    # Session cookie — HttpOnly; Secure + Domain derived from
    # environment AND the request's Origin (so previews at
    # `*.vercel.app` get host-only cookies rather than a Domain the
    # browser would reject).
    attrs = cookie_attrs(settings, request=request)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.session_id,
        max_age=settings.SESSION_ABSOLUTE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
        **attrs,
    )

    # Fresh CSRF cookie (non-HttpOnly so JS can read+echo in X-XSRF-TOKEN).
    raw = generate_token()
    csrf_cookie = sign(raw)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_cookie,
        max_age=settings.SESSION_ABSOLUTE_TTL_SECONDS,
        httponly=False,
        samesite="lax",
        path="/",
        **attrs,
    )

    return LoginResult(session=session, csrf_token=csrf_cookie)


async def do_logout(
    *,
    request: Request,
    response: Response,
    session: SessionData | None,
    store: SessionStore,
    cloud: NdiCloudClient,
) -> None:
    """Terminate the session locally + best-effort on the cloud.

    Audit 2026-04-23 (#55): previously, if ``cloud.logout()`` raised a
    non-network error (``CloudInternalError``, ``Forbidden``, etc.) the
    exception propagated out of this function BEFORE the two
    ``response.delete_cookie`` calls executed. Result: the browser kept
    the session + CSRF cookies for 24 h, producing 401 loops until the
    cookies expired. Logout is a best-effort local operation from the
    caller's perspective — any upstream failure should be logged and
    swallowed so the local teardown completes.
    """
    settings = get_settings()
    # Mirror do_login: per-request Origin decides whether Domain is
    # attached, so the delete-cookie attrs match what was set.
    attrs = cookie_attrs(settings, request=request)
    try:
        if session is not None:
            try:
                await cloud.logout(session.access_token)
            except Exception as e:
                # Best-effort upstream logout — local teardown continues.
                # Truncate session id to 8 chars: the full id IS the
                # session secret (anyone with Railway log access could
                # otherwise replay it by setting the `session` cookie).
                log.info(
                    "auth.logout.cloud_failed",
                    reason=type(e).__name__,
                    session_id=session.session_id[:8],
                )
            # Local session teardown must run even if cloud logout raised.
            await store.delete(session.session_id)
    finally:
        # Cookies are cleared unconditionally — even if everything above
        # failed, the client must exit its authenticated state. The
        # delete-cookie attributes (Domain, Secure) must match the
        # set-cookie attributes from do_login or the browser ignores them.
        response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax", **attrs)
        response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax", **attrs)
