"""Query endpoints — general NDI query and cross-cloud appears-elsewhere."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.query_service import QueryRequest, QueryService
from ._deps import limit_queries, query_service

router = APIRouter(prefix="/api/query", tags=["query"], dependencies=[Depends(limit_queries)])


class AppearsElsewhereBody(BaseModel):
    documentId: str = Field(..., min_length=1, max_length=256)
    excludeDatasetId: str | None = None


@router.post("")
async def run(
    body: QueryRequest,
    svc: Annotated[QueryService, Depends(query_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict:
    return await svc.execute(
        body, access_token=session.access_token if session else None,
    )


@router.post("/appears-elsewhere")
async def appears_elsewhere(
    body: AppearsElsewhereBody,
    svc: Annotated[QueryService, Depends(query_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict:
    results = await svc.appears_elsewhere(
        document_id=body.documentId,
        exclude_dataset_id=body.excludeDatasetId,
        access_token=session.access_token if session else None,
    )
    return {"datasets": results, "totalReferences": sum(int(r.get("count", 0)) for r in results)}
