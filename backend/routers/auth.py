"""Auth endpoints: login, logout, me, CSRF bootstrap."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session, require_session
from ..auth.login import do_login, do_logout
from ..auth.session import SessionData, SessionStore
from ..clients.ndi_cloud import NdiCloudClient
from ..middleware.csrf import CSRF_COOKIE, generate_token, sign
from ..middleware.rate_limit import RateLimiter
from ._deps import cloud, rate_limiter, session_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=256)


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


class CsrfResponse(BaseModel):
    csrfToken: str


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(response: Response) -> CsrfResponse:
    raw = generate_token()
    token = sign(raw)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
        max_age=86400,
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
    response: Response,
    session: Annotated[SessionData | None, Depends(get_current_session)],
    store: Annotated[SessionStore, Depends(session_store)],
    cl: Annotated[NdiCloudClient, Depends(cloud)],
) -> dict[str, bool]:
    await do_logout(response=response, session=session, store=store, cloud=cl)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(
    session: Annotated[SessionData, Depends(require_session)],
) -> MeResponse:
    return MeResponse(
        userId=session.user_id,
        email_hash=session.user_email_hash[:16],
        issuedAt=session.issued_at,
        lastActive=session.last_active,
        expiresAt=session.access_token_expires_at,
        organizationIds=list(session.organization_ids),
        isAdmin=session.is_admin,
    )
