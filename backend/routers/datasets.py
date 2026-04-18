"""Dataset list / detail / class-counts / synthesized summary."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session, require_session
from ..auth.session import SessionData
from ..services.dataset_service import DatasetService
from ..services.dataset_summary_service import (
    DatasetSummary,
    DatasetSummaryService,
)
from ._deps import dataset_service, dataset_summary_service, limit_reads

router = APIRouter(prefix="/api/datasets", tags=["datasets"], dependencies=[Depends(limit_reads)])


@router.get("/published")
async def published(
    svc: Annotated[DatasetService, Depends(dataset_service)],
    summary_svc: Annotated[DatasetSummaryService, Depends(dataset_summary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
    page: int = Query(1, ge=1, le=1000),
    pageSize: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Published catalog with compact :class:`DatasetSummary` embedded per
    row (Plan B B2). Each ``datasets[i]`` gains a ``summary`` key that's
    ``null`` when the synthesizer failed (the UI falls back to raw-record
    rendering). Summaries are produced under a Semaphore-3 fanout — see
    :meth:`DatasetService.list_published_with_summaries`.
    """
    return await svc.list_published_with_summaries(
        page=page,
        page_size=pageSize,
        summary_service=summary_svc,
        session=session,
    )


@router.get("/my")
async def my(
    session: Annotated[SessionData, Depends(require_session)],
    svc: Annotated[DatasetService, Depends(dataset_service)],
    summary_svc: Annotated[DatasetSummaryService, Depends(dataset_summary_service)],
) -> dict[str, Any]:
    """Authenticated ``/my`` list mirroring ``/published`` but over the
    caller's organization's unpublished datasets. Same compact-summary
    shape per row.
    """
    return await svc.list_mine_with_summaries(
        session=session, summary_service=summary_svc,
    )


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
