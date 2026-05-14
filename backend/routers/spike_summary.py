"""Spike-summary endpoint for the experimental /ask chat's
``fetch_spike_summary`` tool and for the data-browser workspace.

POST /api/datasets/{dataset_id}/spike-summary
    Body: SpikeSummaryRequest (camelCase or snake_case fields accepted)

Returns per-unit RAW spike-train data
(``{units: [{name, doc_id, spike_times, isi_intervals}], ...}``).
The TS handler reshapes this into chart_payloads on the chat side; the
workspace consumes raw data directly.

This is a NEW additive endpoint — no schema changes, no existing-route
changes. Read-rate-limited; works for anonymous callers (public
datasets) and logged-in callers (private datasets) via
``get_current_session``.

Soft errors mirror the /signal route: a unit whose spike-times array
fails to parse comes back as a unit entry with ``error`` +
``error_kind`` set, so the chat tool can branch on it without
crashing the whole request.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..clients.ndi_cloud import NdiCloudClient
from ..errors import CloudInternalError, CloudTimeout, CloudUnreachable
from ..observability.logging import get_logger
from ..services.document_service import DocumentService
from ..services.spike_summary_service import (
    SpikeSummaryRequest,
    SpikeSummaryResponse,
    compute_spike_summary,
)
from ._deps import cloud, document_service, limit_reads
from ._validators import DatasetId

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/datasets/{dataset_id}",
    tags=["spike_summary"],
    dependencies=[Depends(limit_reads)],
)


@router.post("/spike-summary")
async def post_spike_summary(
    dataset_id: DatasetId,
    body: SpikeSummaryRequest,
    docs: Annotated[DocumentService, Depends(document_service)],
    cloud_client: Annotated[NdiCloudClient, Depends(cloud)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> SpikeSummaryResponse:
    """Build a spike-summary response for one or more units.

    The body's ``dataset_id`` (alias ``datasetId``) MUST match the path
    parameter — we trust the path for routing and the body for the rest
    of the input. When the body's dataset_id differs we override it to
    the path value so the URL is the single source of truth.
    """
    # URL is source of truth — body might come pre-filled by the TS proxy
    # with an out-of-date id and we don't want to surprise the caller
    # with a 422 over a mismatch they can't see. Override silently.
    if body.dataset_id != dataset_id:
        body = body.model_copy(update={"dataset_id": dataset_id})

    try:
        return await compute_spike_summary(
            body,
            document_service=docs,
            cloud=cloud_client,
            session=session,
        )
    except (CloudInternalError, CloudUnreachable, CloudTimeout) as exc:
        # Translate cloud-layer failures to a typed 503 envelope —
        # matches /tabular_query. Without this, the global handler
        # returns an opaque 500 and the chat tool can't surface a
        # useful error to the LLM.
        from fastapi.responses import JSONResponse
        log.warning(
            "spike_summary.cloud_error",
            dataset_id=dataset_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={
                "units": [],
                "total_matching": 0,
                "kind": body.kind,
                "error": str(exc) or type(exc).__name__,
                "error_kind": "cloud_unavailable",
            },
        )
