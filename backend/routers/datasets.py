"""Dataset list / detail / class-counts / synthesized summary / grain pivot."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.dependencies import get_current_session, require_session
from ..auth.session import SessionData
from ..config import get_settings
from ..services.dataset_provenance_service import (
    DatasetProvenance,
    DatasetProvenanceService,
)
from ..services.dataset_service import DatasetService
from ..services.dataset_summary_service import (
    DatasetSummary,
    DatasetSummaryService,
)
from ..services.pivot_service import PivotService
from ._cancel import cancel_on_disconnect
from ._deps import (
    dataset_provenance_service,
    dataset_service,
    dataset_summary_service,
    limit_reads,
    pivot_service,
)
from ._validators import DatasetId

router = APIRouter(prefix="/api/datasets", tags=["datasets"], dependencies=[Depends(limit_reads)])


@router.get("/published")
async def published(
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
    page: int = Query(1, ge=1, le=1000),
    pageSize: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Published catalog — raw cloud rows only, NO embedded summary.

    Perf fix (2026-04-26): the previous shape called
    :meth:`DatasetService.list_published_with_summaries`, which fanned out
    one :meth:`DatasetSummaryService.build_summary` call per row under
    ``Semaphore(3)`` with no per-row timeout. A single stuck synthesizer
    (cold cache + slow cloud) could burn the FastAPI's full 30s x 3
    retry budget — observed pinning ``/published`` at 90s+ in production.

    The frontend already falls back to raw-record rendering when
    ``dataset.summary`` is absent (see ``components/datasets/DatasetCard.tsx``
    in ``ndi-cloud-app``) and lazily hydrates per-card summaries via the
    edge-cached ``/api/datasets/[id]/summary`` route (60s fresh, 5min SWR
    — PR #84 in ``ndi-cloud-app``). After ONE viewer pays the per-summary
    cost, every other viewer for ~6 minutes gets <50ms.

    The synthesizer fanout is still available via
    :meth:`DatasetService.list_published_with_summaries`, which
    :class:`FacetService` calls during the cross-catalog facet aggregation
    (where the wall-clock budget is intentionally larger).
    """
    return await svc.list_published(page=page, page_size=pageSize)


@router.get("/my")
async def my(
    session: Annotated[SessionData, Depends(require_session)],
    svc: Annotated[DatasetService, Depends(dataset_service)],
    summary_svc: Annotated[DatasetSummaryService, Depends(dataset_summary_service)],
    scope: str = Query(
        "mine",
        pattern="^(mine|all)$",
        description=(
            "`mine` (default): datasets owned by the caller's orgs "
            "(published + in-review + drafts). `all`: ADMIN ONLY — "
            "legacy cross-org in-review firehose via the cloud's "
            "`/datasets/unpublished` admin bypass. Silently treated as "
            "`mine` when the caller isn't admin."
        ),
    ),
) -> dict[str, Any]:
    """Authenticated list — default `scope=mine` returns every dataset
    owned by any org on the caller's session (published + in-review +
    drafts), aggregated via the cloud's ``/organizations/:orgId/datasets``
    endpoint. Admins can opt into the legacy cross-org firehose via
    ``?scope=all``; non-admins requesting ``scope=all`` get silently
    downgraded to the default (no leak of the admin bypass).

    Same compact-summary-per-row shape as ``/published``.
    """
    use_admin_firehose = scope == "all" and session.is_admin
    return await svc.list_mine_with_summaries(
        session=session,
        summary_service=summary_svc,
        admin_all_orgs=use_admin_firehose,
    )


@router.get("/{dataset_id}")
async def detail(
    dataset_id: DatasetId,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.detail(dataset_id, session=session)


@router.get("/{dataset_id}/class-counts")
async def class_counts(
    dataset_id: DatasetId,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.class_counts(dataset_id, session=session)


@router.get("/{dataset_id}/doc-types")
async def doc_types(
    dataset_id: DatasetId,
    svc: Annotated[DatasetService, Depends(dataset_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Alias for /class-counts — matches v1's vocabulary so the ported
    DocumentTypeSelector component keeps working with its existing URL.
    """
    return await svc.class_counts(dataset_id, session=session)


@router.get("/{dataset_id}/summary", response_model=DatasetSummary)
async def summary(
    request: Request,
    dataset_id: DatasetId,
    svc: Annotated[DatasetSummaryService, Depends(dataset_summary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> DatasetSummary:
    """Synthesized, structured dataset summary. See
    :class:`~backend.services.dataset_summary_service.DatasetSummary`
    for the response shape; the frontend mirror is in
    ``frontend/src/types/dataset-summary.ts``.

    Audit 2026-04-23 (#62): wrapped in ``cancel_on_disconnect`` so a
    client navigating away mid-build stops the cloud fan-out instead of
    wasting Lambda time on a response nobody reads.
    """
    return await cancel_on_disconnect(
        request,
        svc.build_summary(dataset_id, session=session),
    )


@router.get("/{dataset_id}/provenance", response_model=DatasetProvenance)
async def provenance(
    request: Request,
    dataset_id: DatasetId,
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

    Audit 2026-04-23 (#62): cancel-on-disconnect wired — cross-dataset
    provenance can touch up to ``_MAX_UNIQUE_TARGETS=1000`` ndiquery
    resolutions on cache miss.
    """
    return await cancel_on_disconnect(
        request,
        svc.build_provenance(dataset_id, session=session),
    )


@router.get("/{dataset_id}/pivot/{grain}")
async def pivot(
    request: Request,
    dataset_id: DatasetId,
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
    return await cancel_on_disconnect(
        request,
        svc.pivot_by_grain(dataset_id, grain, session=session),
    )
