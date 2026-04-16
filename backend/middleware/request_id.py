"""Request-ID middleware.

Generates a 64-bit hex request ID (or honors X-Request-ID if trusted), stores it
in a context var, echoes it on the response header, and makes it available to
error handlers and structlog.
"""
from __future__ import annotations

import re
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ..observability.logging import request_id_ctx

# Accept inbound IDs only if they look trustworthy (hex or dash-separated ASCII).
_INBOUND_RE = re.compile(r"^[A-Za-z0-9_.\-]{8,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inbound = request.headers.get("X-Request-ID")
        rid = inbound if inbound and _INBOUND_RE.match(inbound) else secrets.token_hex(8)
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response
