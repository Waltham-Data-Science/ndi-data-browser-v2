"""Auth endpoints: login, logout, me, CSRF bootstrap, account lifecycle."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..auth.cookie_attrs import cookie_attrs
from ..auth.dependencies import get_current_session, require_session
from ..auth.login import do_login, do_logout
from ..auth.session import SessionData, SessionStore
from ..clients.ndi_cloud import NdiCloudClient
from ..config import get_settings
from ..middleware.csrf import CSRF_COOKIE, generate_token, sign
from ..middleware.rate_limit import Limit, RateLimiter
from ._deps import cloud, rate_limiter, session_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=256)


# --- Account-lifecycle bodies (B3) ---
#
# Email is validated as a plain string at this layer — Cognito does its
# own RFC-5321 format check and the frontend (Yup schema) covers the UX
# side. Adding pydantic's `EmailStr` here would pull in a runtime dep
# (`email-validator`) that isn't otherwise used and that historically
# over-rejects valid Cognito addresses (e.g. plus-tag emails on some
# versions). `min_length=1`/`max_length=N` rejects obviously empty /
# oversize bodies before they waste a cloud round-trip; that's the
# router-side bar. Codes are 6-character Cognito verification codes;
# the upper bound tolerates future format changes.

class SignupBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=256)
    name: str | None = Field(None, max_length=256)


class ForgotPasswordBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=256)


class ResetPasswordBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., min_length=1, max_length=64)
    newPassword: str = Field(..., min_length=1, max_length=256)


class ConfirmEmailBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., min_length=1, max_length=64)


class ResendConfirmationBody(BaseModel):
    email: str = Field(..., min_length=1, max_length=256)


class ChangePasswordBody(BaseModel):
    """Body shape for ``POST /api/auth/change-password``.

    Field names are camelCase to match the frontend's ``changePassword``
    wrapper (``apps/web/lib/api/auth.ts``). The cloud expects a
    different camelCase pair (``oldPassword``/``newPassword``); the
    rename happens server-side in ``NdiCloudClient.change_password``.
    """
    currentPassword: str = Field(..., min_length=1, max_length=256)
    newPassword: str = Field(..., min_length=1, max_length=256)


class MeResponse(BaseModel):
    userId: str
    email_hash: str
    issuedAt: int
    lastActive: int
    expiresAt: int
    # Captured at login from the cloud's `UserWithOrganizationsResult`
    # and cached on the session. Added 2026-04-20 so `/api/datasets/my`
    # can fan out `/organizations/:orgId/datasets` per org and so the
    # frontend can render an admin affordance when relevant.
    organizationIds: list[str] = []
    isAdmin: bool = False
    # Stream 3.4 (2026-05-15): true when this user is allowed to use
    # the /ask chat, given `ENABLE_ASK_ORG_IDS` config + the user's
    # org memberships. Admin users always get true. The frontend
    # hides /ask nav / surfaces a "request access" affordance when
    # this is false. The /api/ask route re-checks server-side so the
    # gate isn't bypassable via DOM tampering.
    canUseAsk: bool = True


class CsrfResponse(BaseModel):
    csrfToken: str


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(request: Request, response: Response) -> CsrfResponse:
    raw = generate_token()
    token = sign(raw)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,
        samesite="lax",
        path="/",
        max_age=86400,
        # `request` lets cookie_attrs read the Origin header so the
        # Domain attribute is only attached when the caller is on
        # `*.ndi-cloud.com`. Preview hosts get host-only cookies that
        # the browser will actually accept.
        **cookie_attrs(get_settings(), request=request),
    )
    return CsrfResponse(csrfToken=token)


@router.post("/login")
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    store: Annotated[SessionStore, Depends(session_store)],
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, object]:
    result = await do_login(
        request=request,
        response=response,
        username=body.username,
        password=body.password,
        store=store,
        cloud=cl,
        limiter=limiter,
    )
    return {
        "ok": True,
        "user": {"id": result.session.user_id},
        "expiresAt": result.session.access_token_expires_at,
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: Annotated[SessionData | None, Depends(get_current_session)],
    store: Annotated[SessionStore, Depends(session_store)],
    cl: Annotated[NdiCloudClient, Depends(cloud)],
) -> dict[str, bool]:
    # `request` is threaded through to do_logout so the delete-cookie
    # attributes match the set-cookie ones (Domain attribute must agree
    # or the browser ignores the clear).
    await do_logout(
        request=request, response=response, session=session, store=store, cloud=cl,
    )
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(
    session: Annotated[SessionData, Depends(require_session)],
) -> MeResponse:
    settings = get_settings()
    return MeResponse(
        userId=session.user_id,
        email_hash=session.user_email_hash[:16],
        issuedAt=session.issued_at,
        lastActive=session.last_active,
        expiresAt=session.access_token_expires_at,
        organizationIds=list(session.organization_ids),
        isAdmin=session.is_admin,
        canUseAsk=settings.user_can_use_ask(
            organization_ids=list(session.organization_ids),
            is_admin=session.is_admin,
        ),
    )


# ---------------------------------------------------------------------------
# Account-lifecycle endpoints (B3)
#
# All five paths:
#  - are unauthenticated (the caller is, by definition, not yet logged in)
#  - are mutating, so CSRF middleware enforces double-submit on each
#  - are per-IP rate-limited with the same envelope as login
#    (RATE_LIMIT_LOGIN_PER_IP_15MIN, default 5/15min) — distinct buckets
#    per endpoint so metrics + dashboards can attribute rejections
#  - return `{ ok: true }` on success (uniform shape for the frontend)
#  - never sign in the caller; signup deliberately requires the user to
#    confirm their email and then call /api/auth/login as the second step.
#    This matches the legacy `accountVerification` flow and avoids
#    creating a session for an unverified account.
# ---------------------------------------------------------------------------

async def _enforce_unauth_ip_limit(
    request: Request, limiter: RateLimiter, *, bucket: str,
) -> None:
    """Per-IP rate-limit shared by the five unauthenticated lifecycle paths.

    Same envelope as `RATE_LIMIT_LOGIN_PER_IP_15MIN` (default 5/15min) but
    each endpoint uses a distinct bucket so abuse of one doesn't lock out
    the others (legitimate user mistypes a reset code → we still let them
    sign up) and so observability can split rejections per flow.
    """
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
    await limiter.check(
        Limit(
            bucket=bucket,
            max_requests=settings.RATE_LIMIT_LOGIN_PER_IP_15MIN,
            window_seconds=15 * 60,
            auth_bucket=True,
        ),
        RateLimiter.subject_for(None, ip),
    )


@router.post("/signup")
async def signup(
    body: SignupBody,
    request: Request,
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, Any]:
    """Proxy `POST /users` on the cloud — create Cognito user + Mongo
    user + default org. Caller must call /api/auth/confirm-email next
    with the code emailed by Cognito; only then can they /api/auth/login.
    """
    await _enforce_unauth_ip_limit(request, limiter, bucket="signup-ip")
    user = await cl.signup(
        email=body.email, password=body.password, name=body.name,
    )
    return {"ok": True, "user": user}


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordBody,
    request: Request,
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, bool]:
    """Initiate a password reset. Always returns `{ ok: true }` — even
    when the email is not registered — so an attacker cannot probe which
    emails have accounts (enumeration resistance). The cloud sends a
    verification code by email; the caller submits it via
    /api/auth/reset-password.
    """
    await _enforce_unauth_ip_limit(request, limiter, bucket="pwreset-ip")
    await cl.forgot_password(email=body.email)
    return {"ok": True}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordBody,
    request: Request,
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, bool]:
    """Submit the verification code emailed by /api/auth/forgot-password
    along with a new password. Returns `{ ok: true }` on success; typed
    INVALID_VERIFICATION_CODE / VERIFICATION_CODE_EXPIRED / WEAK_PASSWORD
    on the canonical Cognito failure modes.
    """
    await _enforce_unauth_ip_limit(request, limiter, bucket="pwreset-confirm-ip")
    await cl.reset_password(
        email=body.email, code=body.code, new_password=body.newPassword,
    )
    return {"ok": True}


@router.post("/confirm-email")
async def confirm_email(
    body: ConfirmEmailBody,
    request: Request,
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, bool]:
    """Confirm a freshly-signed-up account using the verification code
    emailed by Cognito. After this call returns ok, the user can log in.
    Already-verified accounts return EMAIL_ALREADY_VERIFIED so the
    frontend can route the user to the login page rather than re-prompt.
    """
    await _enforce_unauth_ip_limit(request, limiter, bucket="verify-ip")
    await cl.confirm_email(email=body.email, code=body.code)
    return {"ok": True}


@router.post("/resend-confirmation")
async def resend_confirmation(
    body: ResendConfirmationBody,
    request: Request,
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, bool]:
    """Re-send the email-verification code for an unconfirmed account.
    Like /forgot-password, this returns `{ ok: true }` even when the
    email is unknown so it can't be used as an enumeration oracle.
    """
    await _enforce_unauth_ip_limit(request, limiter, bucket="verify-resend-ip")
    await cl.resend_confirmation(email=body.email)
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/auth/change-password (B3 close-out)
#
# Authenticated change-password flow. Distinct from /api/auth/reset-password
# (which is the forgot-password follow-up that takes an emailed code).
# Two-factor verification: session cookie PLUS old password — protects
# against an attacker with a stolen XSRF cookie but no password from
# silently rotating creds.
#
# Per-USER rate limit (RATE_LIMIT_LOGIN_PER_USER_HOUR), not per-IP, since
# the user is authenticated. Subject is the session's user_id so a shared
# NAT or office WiFi doesn't burn another user's quota on this path.
# ---------------------------------------------------------------------------

@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    session: Annotated[SessionData, Depends(require_session)],
    cl: Annotated[NdiCloudClient, Depends(cloud)],
    limiter: Annotated[RateLimiter, Depends(rate_limiter)],
) -> dict[str, bool]:
    """Authenticated change-password — proxies cloud `POST /auth/password`
    with the session's bearer. Cognito codes translate to typed errors:
    NotAuthorizedException → AUTH_INVALID_CREDENTIALS (wrong old pw),
    InvalidPasswordException → WEAK_PASSWORD (new pw fails policy).
    """
    settings = get_settings()
    await limiter.check(
        Limit(
            bucket="change-password-user",
            max_requests=settings.RATE_LIMIT_LOGIN_PER_USER_HOUR,
            window_seconds=60 * 60,
            auth_bucket=True,
        ),
        RateLimiter.subject_for(session.user_id, ip=""),
    )
    await cl.change_password(
        access_token=session.access_token,
        old_password=body.currentPassword,
        new_password=body.newPassword,
    )
    return {"ok": True}
