"""Distribution visualization."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.visualize_service import VisualizeService
from ._deps import limit_reads, visualize_service

router = APIRouter(prefix="/api/visualize", tags=["visualize"], dependencies=[Depends(limit_reads)])


class DistributionBody(BaseModel):
    datasetId: str = Field(..., min_length=1, max_length=64)
    className: str = Field(..., min_length=1, max_length=64)
    field: str = Field(..., min_length=1, max_length=128)
    groupBy: str | None = Field(default=None, max_length=128)


@router.post("/distribution")
async def distribution(
    body: DistributionBody,
    svc: Annotated[VisualizeService, Depends(visualize_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.distribution(
        body.datasetId,
        body.className,
        body.field,
        group_by=body.groupBy,
        session=session,
    )
