"""Dataset list / detail / class-counts / synthesized summary / grain pivot."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import get_current_session, require_session
from ..auth.session import SessionData
from ..config import get_settings
from ..services.dataset_service import DatasetService
from ..services.dataset_summary_service import (
    DatasetSummary,
    DatasetSummaryService,
)
from ..services.pivot_service import PivotService
from ._deps import (
    dataset_service,
    dataset_summary_service,
    limit_reads,
    pivot_service,
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


@router.get("/{dataset_id}/pivot/{grain}")
async def pivot(
    dataset_id: str,
    grain: str,
    svc: Annotated[PivotService, Depends(pivot_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Grain-selectable pivot (Plan B B6e, behind ``FEATURE_PIVOT_V1``).

    - 503 when the feature flag is off (frontend hides the nav on 503).
    - 400 (``VALIDATION_ERROR``) when ``grain`` is not subject/session/element.
    - 404 when the grain has zero docs in this dataset
      (per ``/document-class-counts``) — pre-computed so we don't spend a
      ndiquery on empty grains.
    """
    settings = get_settings()
    if not settings.FEATURE_PIVOT_V1:
        raise HTTPException(
            status_code=503,
            detail=(
                "Grain-selectable pivot is disabled. Set FEATURE_PIVOT_V1=true "
                "to enable."
            ),
        )
    return await svc.pivot_by_grain(dataset_id, grain, session=session)
