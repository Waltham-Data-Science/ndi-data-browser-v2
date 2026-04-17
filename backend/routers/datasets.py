"""Dataset list / detail / class-counts."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session, require_session
from ..auth.session import SessionData
from ..services.dataset_service import DatasetService
from ._deps import dataset_service, limit_reads

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
    return await svc.list_mine(access_token=session.access_token, user_id=session.user_id)


@router.get("/{dataset_id}")
async def detail(
    dataset_id: str,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.detail(
        dataset_id,
        access_token=session.access_token if session else None,
    )


@router.get("/{dataset_id}/class-counts")
async def class_counts(
    dataset_id: str,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.class_counts(
        dataset_id,
        access_token=session.access_token if session else None,
    )


@router.get("/{dataset_id}/doc-types")
async def doc_types(
    dataset_id: str,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Alias for /class-counts — matches v1's vocabulary so the ported
    DocumentTypeSelector component keeps working with its existing URL.
    """
    return await svc.class_counts(
        dataset_id,
        access_token=session.access_token if session else None,
    )
