"""Dataset list / detail / class-counts / synthesized summary."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session, require_session
from ..auth.session import SessionData
from ..services.dataset_provenance_service import (
    DatasetProvenance,
    DatasetProvenanceService,
)
from ..services.dataset_service import DatasetService
from ..services.dataset_summary_service import (
    DatasetSummary,
    DatasetSummaryService,
)
from ._deps import (
    dataset_provenance_service,
    dataset_service,
    dataset_summary_service,
    limit_reads,
)

router = APIRouter(prefix="/api/datasets", tags=["datasets"], dependencies=[Depends(limit_reads)])


@router.get("/published")
async def published(
    svc: Annotated[DatasetService, Depends(dataset_service)],
    page: int = Query(1, ge=1, le=1000),
    pageSize: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return await svc.list_published(page=page, page_size=pageSize)


@router.get("/my")
async def my(
    session: Annotated[SessionData, Depends(require_session)],
    svc: Annotated[DatasetService, Depends(dataset_service)],
) -> dict[str, Any]:
    return await svc.list_mine(session=session)


@router.get("/{dataset_id}")
async def detail(
    dataset_id: str,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.detail(dataset_id, session=session)


@router.get("/{dataset_id}/class-counts")
async def class_counts(
    dataset_id: str,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.class_counts(dataset_id, session=session)


@router.get("/{dataset_id}/doc-types")
async def doc_types(
    dataset_id: str,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Alias for /class-counts — matches v1's vocabulary so the ported
    DocumentTypeSelector component keeps working with its existing URL.
    """
    return await svc.class_counts(dataset_id, session=session)


@router.get("/{dataset_id}/summary", response_model=DatasetSummary)
async def summary(
    dataset_id: str,
    svc: Annotated[DatasetSummaryService, Depends(dataset_summary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> DatasetSummary:
    """Synthesized, structured dataset summary. See
    :class:`~backend.services.dataset_summary_service.DatasetSummary`
    for the response shape; the frontend mirror is in
    ``frontend/src/types/dataset-summary.ts``.
    """
    return await svc.build_summary(dataset_id, session=session)


@router.get("/{dataset_id}/provenance", response_model=DatasetProvenance)
async def provenance(
    dataset_id: str,
    svc: Annotated[
        DatasetProvenanceService, Depends(dataset_provenance_service),
    ],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> DatasetProvenance:
    """Dataset provenance / derivation graph (Plan B B5).

    Aggregates three signals:

    - ``branchOf``: parent dataset this one was forked from.
    - ``branches``: child datasets forked off this one.
    - ``documentDependencies``: per-class cross-dataset ``depends_on`` edge
      counts sourced from scanning every document in the dataset.

    See :class:`~backend.services.dataset_provenance_service.DatasetProvenance`
    for the response shape; the frontend mirror is in
    ``frontend/src/types/dataset-provenance.ts``.
    """
    return await svc.build_provenance(dataset_id, session=session)
