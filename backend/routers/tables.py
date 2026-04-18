"""Summary tables — single class, combined, and ontologyTableRow grouping."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.summary_table_service import SummaryTableService
from ._deps import limit_reads, summary_table_service

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
    dataset_id: str,
    svc: Annotated[SummaryTableService, Depends(summary_table_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.combined(dataset_id, session=session)


@router.get("/ontology")
async def ontology_tables(
    dataset_id: str,
    svc: Annotated[SummaryTableService, Depends(summary_table_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Group `ontologyTableRow` docs by their `variableNames` schema.
    See `SummaryTableService.ontology_tables` for the response shape.
    """
    return await svc.ontology_tables(dataset_id, session=session)


@router.get("/{class_name}")
async def single(
    dataset_id: str,
    class_name: str,
    svc: Annotated[SummaryTableService, Depends(summary_table_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    if class_name not in SUPPORTED_CLASSES and class_name != "combined":
        raise HTTPException(status_code=400, detail=f"Unsupported table class: {class_name}")
    return await svc.single_class(dataset_id, class_name, session=session)
