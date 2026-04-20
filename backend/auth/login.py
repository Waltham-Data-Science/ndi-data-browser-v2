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
    log.info("auth.login.success", session_id=session.session_id)

    # Session cookie — HttpOnly, Secure.
    secure = settings.ENVIRONMENT != "development"
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.session_id,
        max_age=settings.SESSION_ABSOLUTE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )

    # Fresh CSRF cookie (non-HttpOnly so JS can read+echo in X-XSRF-TOKEN).
    raw = generate_token()
    csrf_cookie = sign(raw)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_cookie,
        max_age=settings.SESSION_ABSOLUTE_TTL_SECONDS,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )

    return LoginResult(session=session, csrf_token=csrf_cookie)


async def do_logout(
    *,
    response: Response,
    session: SessionData | None,
    store: SessionStore,
    cloud: NdiCloudClient,
) -> None:
    settings = get_settings()
    if session is not None:
        try:
            await cloud.logout(session.access_token)
        finally:
            await store.delete(session.session_id)

    secure = settings.ENVIRONMENT != "development"
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="lax")
