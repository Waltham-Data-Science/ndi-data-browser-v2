"""Per-environment cookie attribute helper.

Centralizes the ``Set-Cookie`` / ``Delete-Cookie`` attribute set used by
the session and CSRF cookies.

Domain attribute
----------------

Production carries ``Domain=.ndi-cloud.com`` ONLY when the request
originates from ``*.ndi-cloud.com`` so the apex Vercel deployment can
read cookies issued by the Railway backend (cross-repo unification,
Phase 4).

Vercel **preview** deployments at ``*.vercel.app`` get host-only
cookies. A Set-Cookie that carries ``Domain=.ndi-cloud.com`` on a
response served back to a non-``ndi-cloud.com`` host is silently
rejected by the browser — the cookie spec forbids servers from
setting cookies for domains they don't control. That's why
preview-time login was breaking with ``CSRF_INVALID`` errors before
this fix (2026-05-14 tutorial-parity smoke).

Other attributes
----------------

Dev keeps host-only + insecure for plain-HTTP localhost. Staging (and
any other ENVIRONMENT value) is host-only + secure.
"""
from typing import Any
from urllib.parse import urlparse

from fastapi import Request

from ..config import Settings


def cookie_attrs(settings: Settings, *, request: Request) -> dict[str, Any]:
    """Return the Set-Cookie attribute dict for the current env + request.

    The ``request`` parameter is required: the per-request Origin (or
    Referer) is what decides whether the Domain attribute is safe to
    attach. Old callers that passed only ``settings`` must be updated —
    silently guessing wrong is what broke preview login.
    """
    if settings.ENVIRONMENT == "production":
        if _request_from_ndi_cloud(request):
            return {"secure": True, "domain": ".ndi-cloud.com"}
        # Preview / vercel.app / anything else served by the production
        # backend: secure but host-only. The browser will accept these
        # because the cookie's implicit Domain matches the response
        # origin (the preview hostname).
        return {"secure": True}
    return {"secure": settings.ENVIRONMENT != "development"}


def _request_from_ndi_cloud(request: Request) -> bool:
    """Was this request issued by a browser tab on ``*.ndi-cloud.com``?

    Reads the Origin header (browsers set this on every cross-site and
    every same-origin POST since 2020), with a fallback to Referer for
    older clients and the few same-origin GETs that omit Origin.
    Returns True only if the URL's hostname is exactly
    ``ndi-cloud.com`` or a subdomain of it.

    Returns False when:
        - both Origin and Referer are missing or unparseable
        - the host doesn't end with ``ndi-cloud.com`` (i.e. preview)
    """
    for header_name in ("origin", "referer"):
        raw = request.headers.get(header_name)
        if not raw:
            continue
        try:
            parts = urlparse(raw)
        except ValueError:
            continue
        if not parts.netloc:
            continue
        host = parts.netloc.split(":", 1)[0].lower()
        if host == "ndi-cloud.com" or host.endswith(".ndi-cloud.com"):
            return True
    return False
