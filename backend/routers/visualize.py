"""Distribution visualization."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.visualize_service import VisualizeService
from ._deps import limit_reads, visualize_service
from ._validators import DATASET_ID_PATTERN

router = APIRouter(prefix="/api/visualize", tags=["visualize"], dependencies=[Depends(limit_reads)])

# Audit 2026-04-23 (#54): path-injection hardening. Previously
# ``DistributionBody.datasetId`` had only a length bound. The value flows
# into ``f"/datasets/{dataset_id}/document-class-counts"`` on the
# upstream cloud URL, which the cloud trusts the proxy to validate. A
# body like ``{"datasetId": "foo/bar"}`` would pivot into sibling
# resources. Matching the shared ``DATASET_ID_PATTERN`` closes this and
# keeps the validator list single-sourced.
_CLASSNAME_PATTERN = r"^[a-zA-Z0-9_]{1,64}$"
# Class fields are dotted attribute paths (e.g. ``studyMetadata.species``,
# ``files.file_info.locations.location``). Alphanumeric + underscore +
# dot. No whitespace, no path segments, no quote characters.
_FIELD_PATTERN = r"^[a-zA-Z0-9_.]{1,128}$"


class DistributionBody(BaseModel):
    datasetId: str = Field(..., min_length=1, max_length=128, pattern=DATASET_ID_PATTERN)
    className: str = Field(..., min_length=1, max_length=64, pattern=_CLASSNAME_PATTERN)
    field: str = Field(..., min_length=1, max_length=128, pattern=_FIELD_PATTERN)
    groupBy: str | None = Field(default=None, max_length=128, pattern=_FIELD_PATTERN)


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
