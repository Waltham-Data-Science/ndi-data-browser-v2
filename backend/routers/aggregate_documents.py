"""Aggregate-documents endpoint — Stream 4.9 (2026-05-16).

POST /api/aggregate-documents → run an ndi_query and aggregate a numeric
field across the matches. Auth-optional: anonymous requests get the
public-dataset slice; authenticated requests get the user's org reach
(propagated via the inbound session). Rate-limited under
``limit_queries`` (heavier than reads — the cloud may scan up to 50K
docs).

Closes ADR-001 compliance debt: the old TS handler ran the whole loop
on Vercel; this router moves it to the right runtime.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.aggregate_documents_service import (
    AggregateDocumentsRequest,
    AggregateDocumentsService,
)
from ._deps import aggregate_documents_service, limit_queries

router = APIRouter(
    prefix="/api/aggregate-documents",
    tags=["query"],
    dependencies=[Depends(limit_queries)],
)


@router.post("")
async def aggregate(
    body: AggregateDocumentsRequest,
    svc: Annotated[
        AggregateDocumentsService, Depends(aggregate_documents_service),
    ],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.aggregate(
        body, access_token=session.access_token if session else None,
    )
