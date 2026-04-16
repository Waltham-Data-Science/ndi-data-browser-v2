"""Document list / detail."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.document_service import DocumentService
from ._deps import document_service, limit_reads

router = APIRouter(prefix="/api/datasets/{dataset_id}/documents", tags=["documents"], dependencies=[Depends(limit_reads)])


@router.get("")
async def list_docs(
    dataset_id: str,
    svc: Annotated[DocumentService, Depends(document_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
    class_name: str | None = Query(default=None, alias="class"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
) -> dict:
    return await svc.list_by_class(
        dataset_id=dataset_id,
        class_name=class_name,
        page=page,
        page_size=pageSize,
        access_token=session.access_token if session else None,
    )


@router.get("/{document_id}")
async def detail(
    dataset_id: str,
    document_id: str,
    svc: Annotated[DocumentService, Depends(document_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict:
    return await svc.detail(
        dataset_id, document_id, access_token=session.access_token if session else None,
    )
