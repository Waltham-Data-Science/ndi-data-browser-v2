"""Per-environment cookie attribute helper.

Centralizes the ``Set-Cookie`` / ``Delete-Cookie`` attribute set used by
the session and CSRF cookies. Production carries
``Domain=.ndi-cloud.com`` so the apex Vercel deployment can read cookies
issued by the Railway backend after the cross-repo unification (Phase
4); dev keeps host-only + insecure for plain-HTTP localhost; everything
else (e.g. staging) is host-only + secure.
"""
from typing import Any

from ..config import Settings


def cookie_attrs(settings: Settings) -> dict[str, Any]:
    if settings.ENVIRONMENT == "production":
        return {"secure": True, "domain": ".ndi-cloud.com"}
    return {"secure": settings.ENVIRONMENT != "development"}
