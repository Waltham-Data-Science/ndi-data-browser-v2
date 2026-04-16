"""Per-request Prometheus metrics middleware."""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ..observability.metrics import (
    http_request_duration_seconds,
    http_requests_total,
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            # Route template avoids cardinality explosion.
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path) if route else request.url.path
            http_requests_total.labels(
                method=request.method, route=route_path, status=str(status),
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, route=route_path,
            ).observe(time.perf_counter() - t0)
        return response
