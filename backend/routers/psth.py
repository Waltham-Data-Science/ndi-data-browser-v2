"""PSTH endpoint for the experimental /ask chat's ``fetch_psth`` tool
and for the data-browser workspace.

POST /api/datasets/{dataset_id}/psth
    Body: PsthRequest (camelCase or snake_case fields accepted)

Returns a peri-stimulus time histogram for one unit + one stimulus
document. Response shape::

    {
      bin_centers, counts, mean_rate_hz,
      n_trials, n_spikes,
      bin_size_ms, t0, t1,
      unit_name, unit_doc_id, stimulus_doc_id,
      per_trial_raster?,         # only when include_raster=True
      error?, error_kind?,       # soft-error envelope
    }

This is a NEW additive endpoint — no schema changes, no existing-route
changes. Read-rate-limited; works for anonymous callers (public
datasets) and logged-in callers (private datasets) via
``get_current_session``.

Soft errors mirror /signal and /spike-summary: when the unit doc fails
to decode, the stimulus doc carries no event timestamps, or every
window comes back empty, the response is a valid (but zero-filled or
empty) histogram with ``error`` + ``error_kind`` set so the chat tool
can branch on it.

Cloud-tier hard failures (Railway can't reach ndi-cloud-node, etc.)
translate to a 503 envelope at the HTTP boundary — same pattern as
/spike-summary.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..errors import CloudInternalError, CloudTimeout, CloudUnreachable
from ..observability.logging import get_logger
from ..services.binary_service import BinaryService
from ..services.document_service import DocumentService
from ..services.psth_service import (
    PsthRequest,
    PsthResponse,
    compute_psth,
)
from ._deps import binary_service, document_service, limit_reads
from ._validators import DatasetId

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/datasets/{dataset_id}",
    tags=["psth"],
    dependencies=[Depends(limit_reads)],
)


@router.post("/psth")
async def post_psth(
    dataset_id: DatasetId,
    body: PsthRequest,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> PsthResponse:
    """Build a peri-stimulus time histogram for one unit + one stimulus.

    The body's ``unit_doc_id`` and ``stimulus_doc_id`` must both be
    24-char Mongo ObjectIds (pydantic enforces min_length only; the
    document service resolves ndiId form if the caller passes one
    transparently).
    """
    try:
        return await compute_psth(
            body,
            document_service=docs,
            binary_service=bs,
            session=session,
            dataset_id=dataset_id,
        )
    except (CloudInternalError, CloudUnreachable, CloudTimeout) as exc:
        # Translate cloud-layer failures to a typed 503 envelope —
        # matches /spike-summary. Without this, the global handler
        # returns an opaque 500 and the chat tool can't surface a
        # useful error to the LLM.
        from fastapi.responses import JSONResponse
        log.warning(
            "psth.cloud_error",
            dataset_id=dataset_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={
                "bin_centers": [],
                "counts": [],
                "mean_rate_hz": [],
                "n_trials": 0,
                "n_spikes": 0,
                "bin_size_ms": body.bin_size_ms,
                "t0": body.t0,
                "t1": body.t1,
                "unit_name": "",
                "unit_doc_id": body.unit_doc_id,
                "stimulus_doc_id": body.stimulus_doc_id,
                "per_trial_raster": None,
                "error": str(exc) or type(exc).__name__,
                "error_kind": "cloud_unavailable",
            },
        )
