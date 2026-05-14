"""ndi_dataset router — Sprint 1.5 cloud-backed dataset binding endpoint.

GET /api/datasets/{dataset_id}/ndi_overview
    Returns a high-level summary (element / subject / epoch counts +
    element listing) computed by traversing a LOCAL
    ``ndi.dataset.Dataset`` materialized from the cloud's documents.

Failure posture (deliberate): when the binding can't produce a value —
NDI-python missing, downloadDataset timed out, cloud unreachable,
anything — the endpoint returns **HTTP 503** with a JSON envelope so
the chat tool can gracefully fall back to its existing ``ndi_query``
path. Callers should NOT treat 503 as a hard failure.

Why a separate router rather than folding into ``datasets.py``:
1. The Sprint 1.5 binding is OPTIONAL infrastructure — keeping it in
   its own module makes it trivial to disable (just unmount the
   router) if the cloud auth / Mongo download path fails in
   production.
2. The endpoint has a different latency posture (cold loads up to
   90s) — visible isolation helps with metrics + rate-limit reasoning.
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..observability.logging import get_logger
from ..services.dataset_binding_service import DatasetBindingService
from ._deps import dataset_binding_service, limit_reads
from ._validators import DatasetId

log = get_logger(__name__)


# Per-call wall-clock cap. Cold loads can take 10-30s for the demo
# datasets; we allow up to 60s before surfacing a 503 so the chat
# doesn't hang. The service's own ``COLD_LOAD_TIMEOUT_SECONDS`` is 90s
# — that's the BACKGROUND limit (warm/pre-warm tasks). This per-
# request cap is stricter so a user-facing request never blocks the
# router for ~90s.
REQUEST_TIMEOUT_SECONDS = 60.0


router = APIRouter(
    prefix="/api/datasets/{dataset_id}",
    tags=["ndi_dataset"],
    dependencies=[Depends(limit_reads)],
)


@router.get("/ndi_overview")
async def ndi_overview(
    dataset_id: DatasetId,
    svc: Annotated[DatasetBindingService, Depends(dataset_binding_service)],
) -> Any:
    """High-level dataset summary computed by NDI-python's SDK.

    Returns a dict shape on success:

        {
          element_count: int,
          subject_count: int,
          epoch_count: int,
          elements: [{name, type}],          # capped at 50
          elements_truncated: bool,
          reference: str,
          cache_hit: bool,
          cache_age_seconds: float,
        }

    Returns 503 with ``{error, reason}`` on any failure (binding
    unavailable, cold-load timeout, cloud unreachable). The chat tool
    layer translates 503 → graceful fallback prompt.
    """
    try:
        result = await asyncio.wait_for(
            svc.overview(dataset_id),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log.warning(
            "ndi_dataset.overview.request_timeout",
            dataset_id=dataset_id,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "dataset binding unavailable",
                "reason": (
                    f"overview computation exceeded {REQUEST_TIMEOUT_SECONDS:.0f}s "
                    "wall clock; try again in a moment"
                ),
            },
        )
    except Exception as exc:  # blind — must not 500 a user request
        log.warning(
            "ndi_dataset.overview.unexpected_failure",
            dataset_id=dataset_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "dataset binding unavailable",
                "reason": "binding raised an unexpected error",
            },
        )

    if result is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "dataset binding unavailable",
                "reason": (
                    "NDI-python dataset materialization failed or is not "
                    "configured on this server"
                ),
            },
        )
    return result
