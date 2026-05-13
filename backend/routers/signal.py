"""Signal endpoint for the experimental /ask chat's ``fetch_signal`` tool.

GET /api/datasets/{dataset_id}/documents/{document_id}/signal
    ?downsample=N      (max points per channel; default 2000, max 5000)
    &t0=FLOAT          (start time in seconds; default = first sample)
    &t1=FLOAT          (end time in seconds; default = last sample)

The route reuses :class:`BinaryService.get_timeseries` to decode the
underlying binary (NBF / VHSB), then trims by [t0, t1] and applies
LTTB downsampling to bound the response size.

This is a NEW additive endpoint — no schema changes, no existing-route
changes. Merging it to ``main`` deploys it to Railway alongside the
existing ``/data/timeseries`` route (which the Document Explorer chart
view continues to use unchanged).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.binary_service import BinaryService
from ..services.document_service import DocumentService
from ..services.signal_service import (
    DEFAULT_DOWNSAMPLE_POINTS,
    MAX_DOWNSAMPLE_POINTS,
    downsample_timeseries,
)
from ._deps import binary_service, document_service, limit_reads
from ._validators import DatasetId, DocumentId

router = APIRouter(
    prefix="/api/datasets/{dataset_id}/documents/{document_id}",
    tags=["signal"],
    dependencies=[Depends(limit_reads)],
)


@router.get("/signal")
async def get_signal(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
    downsample: Annotated[
        int,
        Query(
            ge=10,
            le=MAX_DOWNSAMPLE_POINTS,
            description=(
                "Maximum number of points per channel after LTTB downsampling. "
                f"Defaults to {DEFAULT_DOWNSAMPLE_POINTS}."
            ),
        ),
    ] = DEFAULT_DOWNSAMPLE_POINTS,
    t0: Annotated[
        float | None,
        Query(description="Trim start time in seconds; defaults to first sample."),
    ] = None,
    t1: Annotated[
        float | None,
        Query(description="Trim end time in seconds; defaults to last sample."),
    ] = None,
    file: Annotated[
        str | None,
        Query(
            description=(
                "Optional file-name selector for multi-file documents. "
                "Substring-matched against the document's file_list. "
                "Useful for daqreader_mfdaq_epochdata_ingested docs "
                "where the alphabetically first file is metadata "
                "(channel_list.bin) — pass e.g. 'ai_group1_seg.nbf_1' "
                "to grab the analog-input voltage trace. When omitted, "
                "the first file in the list is used."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Return a downsampled timeseries with provenance.

    Response shape (success):
        {
          "channels": {ch_name: [float|null, ...]},
          "timestamps": [float, ...],
          "sample_count": int,
          "format": "nbf" | "vhsb",
          "error": null,
          "downsampled": bool,
          "original_sample_count": int,
          "t0_seconds": float | null,
          "t1_seconds": float | null,
          "source": {
            "dataset_id": str,
            "document_id": str,
            "doc_class": str | null,
            "doc_name": str | null,
          }
        }

    Response shape (soft error — same envelope as /data/timeseries):
        {"channels": {}, "timestamps": null, "sample_count": 0,
         "format": "", "error": "...", "errorKind": "...", "source": {...}}
    """
    document = await docs.detail(
        dataset_id, document_id,
        access_token=session.access_token if session else None,
    )

    # Reuse the existing decoder. On soft errors (missing file, vlt
    # library unavailable, unsupported format) it returns an error
    # payload that we surface as-is alongside the source provenance.
    timeseries = await bs.get_timeseries(
        document,
        access_token=session.access_token if session else None,
        filename=file,
    )
    result = downsample_timeseries(timeseries, downsample, t0, t1)

    # Provenance — gives the chatbot enough information to cite the
    # exact NDI document the signal came from.
    base = document.get("base", {}) if isinstance(document, dict) else {}
    doc_class = None
    if isinstance(document, dict):
        cls = document.get("document_class") or {}
        doc_class = cls.get("classname") if isinstance(cls, dict) else None
    result["source"] = {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "doc_class": doc_class,
        "doc_name": base.get("name") if isinstance(base, dict) else None,
    }
    return result
