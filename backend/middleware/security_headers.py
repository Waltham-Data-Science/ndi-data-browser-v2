"""Security headers middleware.

CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
Permissions-Policy, Strict-Transport-Security. Plus optional CSP
violation reporting via the legacy `report-uri` directive AND the
modern Reporting API (`report-to` directive + `Report-To` header) —
both gated on `settings.CSP_REPORT_URI`.
"""
from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ..config import get_settings

# Reporting API group name. The CSP `report-to` directive references this
# group; the `Report-To` response header maps the group to the URL.
_CSP_REPORT_GROUP = "csp-endpoint"

# `Report-To` max_age (seconds the browser caches the endpoint mapping).
# 12 weeks matches the value in MDN's reference example for Reporting
# API and is short enough that a URL rotation propagates within a
# quarter without leaving a stale endpoint reference for years.
_REPORT_TO_MAX_AGE = 12 * 7 * 24 * 60 * 60  # ≈ 7,257,600 s


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        settings = get_settings()
        origins = settings.cors_origins_list
        connect_src = "'self' " + " ".join(origins) + " " + settings.cloud_base_url.replace("/v1", "")
        csp_parts = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https:",
            "media-src 'self' blob: https:",
            "font-src 'self' data:",
            f"connect-src {connect_src}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
        ]
        # O2: append CSP-reporting directives only when an endpoint is
        # configured. Both `report-uri` (legacy) and `report-to` (modern)
        # are emitted so the browser picks whichever it understands.
        if settings.CSP_REPORT_URI:
            csp_parts.append(f"report-uri {settings.CSP_REPORT_URI}")
            csp_parts.append(f"report-to {_CSP_REPORT_GROUP}")
            self._report_to_header: str | None = json.dumps(
                {
                    "group": _CSP_REPORT_GROUP,
                    "max_age": _REPORT_TO_MAX_AGE,
                    "endpoints": [{"url": settings.CSP_REPORT_URI}],
                },
                separators=(",", ":"),  # compact — wire-friendly
            )
        else:
            self._report_to_header = None
        self._csp = "; ".join(csp_parts) + ";"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self._csp
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # O2: emit the Report-To header alongside CSP so the modern
        # Reporting API path can deliver violation reports. Skipped when
        # CSP_REPORT_URI is unset.
        if self._report_to_header is not None:
            response.headers["Report-To"] = self._report_to_header
        return response
