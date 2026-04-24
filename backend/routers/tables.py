"""Summary tables — single class, combined, and ontologyTableRow grouping.

All endpoints are wrapped in ``cancel_on_disconnect`` (audit 2026-04-23 #62)
— a ``/tables/combined`` build on a cold cache can touch ~19 bulk-fetch
batches × ~30s Lambda ceiling. A client navigating away mid-build used
to keep burning cloud calls; now it unwinds.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.summary_table_service import SummaryTableService
from ._cancel import cancel_on_disconnect
from ._deps import limit_reads, summary_table_service
from ._validators import DatasetId

router = APIRouter(
    prefix="/api/datasets/{dataset_id}/tables",
    tags=["tables"],
    dependencies=[Depends(limit_reads)],
)

# Classes accepted by single_class. `ontologyTableRow` is handled by a
# dedicated endpoint (`/tables/ontology`) because its shape differs.
SUPPORTED_CLASSES = {
    "subject", "probe", "epoch", "element", "element_epoch",
    "treatment", "openminds", "openminds_subject", "probe_location",
}


@router.get("/combined")
async def combined(
    request: Request,
    dataset_id: DatasetId,
    svc: Annotated[SummaryTableService, Depends(summary_table_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await cancel_on_disconnect(
        request,
        svc.combined(dataset_id, session=session),
    )


@router.get("/ontology")
async def ontology_tables(
    request: Request,
    dataset_id: DatasetId,
    svc: Annotated[SummaryTableService, Depends(summary_table_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Group `ontologyTableRow` docs by their `variableNames` schema.
    See `SummaryTableService.ontology_tables` for the response shape.
    """
    return await cancel_on_disconnect(
        request,
        svc.ontology_tables(dataset_id, session=session),
    )


@router.get("/{class_name}")
async def single(
    request: Request,
    dataset_id: DatasetId,
    class_name: str,
    svc: Annotated[SummaryTableService, Depends(summary_table_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    if class_name not in SUPPORTED_CLASSES and class_name != "combined":
        raise HTTPException(status_code=400, detail=f"Unsupported table class: {class_name}")
    return await cancel_on_disconnect(
        request,
        svc.single_class(dataset_id, class_name, session=session),
    )
