"""Treatment-timeline endpoint — Gantt-style horizontal projection of
treatment docs for a dataset.

POST /api/datasets/{dataset_id}/treatment-timeline

The Next.js chat tool layer used to own this orchestration in
``apps/web/lib/ndi/tools/treatment-timeline.ts``. We're moving the
heart of NDI processing to Railway/Python so the work lives next to
ndi-python; the TS handler shrinks to a thin proxy that forwards
``{datasetId, title, maxSubjects}`` to this endpoint and reshapes the
raw response into the chat-specific ``chart_payload`` envelope.

Schema compatibility
────────────────────
The pydantic body accepts BOTH camelCase (``datasetId``,
``maxSubjects``) and snake_case (``dataset_id``, ``max_subjects``) via
field aliases. The TS proxy sends camelCase; future Python callers
(e.g. the workspace) may prefer snake_case. Both flow through the
same model.

Error posture
─────────────
We DELIBERATELY do not surface cloud-error envelopes (e.g. 503) here.
The service catches its own internal failures and returns an
``empty_hint`` envelope instead, so callers always get a well-typed
response shape even when one of the two backends is degraded. If
both primary AND fallback are zero, ``empty_hint`` is set; the chart
renders an empty state and the chat tells the user plainly.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..observability.logging import get_logger
from ..services.summary_table_service import SummaryTableService
from ..services.tabular_query_service import TabularQueryService
from ..services.treatment_timeline_service import (
    DEFAULT_MAX_SUBJECTS,
    HARD_CAP_MAX_SUBJECTS,
    TreatmentTimelineService,
)
from ._deps import limit_reads, summary_table_service, tabular_query_service
from ._validators import DatasetId

log = get_logger(__name__)


router = APIRouter(
    prefix="/api/datasets/{dataset_id}",
    tags=["treatment_timeline"],
    dependencies=[Depends(limit_reads)],
)


class TreatmentTimelineRequest(BaseModel):
    """Body for ``POST /api/datasets/{id}/treatment-timeline``.

    Field aliases let the model accept BOTH camelCase (the TS proxy)
    AND snake_case (future Python callers) without forcing the caller
    to pick a side.
    """

    # ``populate_by_name`` lets us submit either the alias OR the
    # underlying name; the response model serializes by alias.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=160,
        description="Optional chart title; passed through verbatim.",
    )
    max_subjects: int = Field(
        default=DEFAULT_MAX_SUBJECTS,
        alias="maxSubjects",
        gt=0,
        le=HARD_CAP_MAX_SUBJECTS,
        description=(
            f"Max distinct subjects in the chart. Default "
            f"{DEFAULT_MAX_SUBJECTS}, hard cap {HARD_CAP_MAX_SUBJECTS}. "
            "Beyond that the chart becomes a wall of bars."
        ),
    )


def treatment_timeline_service(
    summary: Annotated[SummaryTableService, Depends(summary_table_service)],
    tabular: Annotated[TabularQueryService, Depends(tabular_query_service)],
) -> TreatmentTimelineService:
    """DI factory — composes the orchestration service from the two
    underlying services that already have their own DI graph wired
    on ``app.state``. No new app-state caches required.
    """
    return TreatmentTimelineService(summary=summary, tabular=tabular)


@router.post("/treatment-timeline")
async def treatment_timeline(
    dataset_id: DatasetId,
    body: TreatmentTimelineRequest,
    svc: Annotated[TreatmentTimelineService, Depends(treatment_timeline_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Compute the treatment timeline for ``dataset_id``.

    Public/anonymous-readable for public datasets; honors session
    cookies for private dataset access (matches the rest of v2's
    read surface). Rate-limited under the standard ``reads`` bucket.

    On both primary AND fallback being empty, returns the
    well-typed response body with ``empty_hint`` set — does NOT
    raise an error. Frontend callers render an empty state and the
    chat surfaces the reason in prose.
    """
    return await svc.compute_timeline(
        dataset_id,
        title=body.title,
        max_subjects=body.max_subjects,
        session=session,
    )
