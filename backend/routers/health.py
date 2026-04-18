"""Health endpoints for Railway + Kubernetes-style liveness/readiness."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..observability.metrics import CONTENT_TYPE, metrics_bytes

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/health/ready")
async def ready(request: Request) -> dict[str, object]:
    redis_ok = True
    cloud_ok = True
    try:
        await request.app.state.redis.ping()
    except Exception:
        redis_ok = False
    # We consider the cloud "ready" if the httpx client is initialized;
    # a real reachability probe would add latency to every Railway healthcheck.
    try:
        _ = request.app.state.cloud_client.client
    except Exception:
        cloud_ok = False
    status = "ok" if redis_ok and cloud_ok else "degraded"
    return {"status": status, "redis": redis_ok, "cloud": cloud_ok}


@router.get("/metrics")
async def metrics() -> object:
    from fastapi import Response as FastAPIResponse
    return FastAPIResponse(content=metrics_bytes(), media_type=CONTENT_TYPE)


@router.get("/api/health/version")
async def version() -> dict[str, object]:
    """Expose app version + environment for dashboards."""
    from ..config import get_settings
    s = get_settings()
    return {
        "version": "2.0.0",
        "environment": s.ENVIRONMENT,
    }
