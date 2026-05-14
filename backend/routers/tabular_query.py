"""Tabular-query endpoint for the experimental /ask chat's
``tabular_query`` tool + ``ViolinChart`` component.

GET /api/datasets/{dataset_id}/tabular_query
    ?variableNameContains=ElevatedPlusMaze    (required substring)
    &groupBy=treatment_group                  (optional grouping col)
    &groupOrder=Saline,CNO                    (optional CSV order)

Returns per-group summary stats + raw values for a violin / jitter
plot. See :mod:`backend.services.tabular_query_service` for the
aggregation logic.

This is a NEW additive endpoint — no schema changes, no existing-
route changes. Anonymous-readable (matches the read posture of the
rest of v2's surface).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..errors import CloudInternalError, CloudTimeout, CloudUnreachable
from ..observability.logging import get_logger
from ..services.tabular_query_service import TabularQueryService
from ._deps import limit_reads, tabular_query_service
from ._validators import DatasetId

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/datasets/{dataset_id}",
    tags=["tabular_query"],
    dependencies=[Depends(limit_reads)],
)


@router.get("/tabular_query")
async def tabular_query(
    dataset_id: DatasetId,
    svc: Annotated[TabularQueryService, Depends(tabular_query_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
    variableNameContains: Annotated[
        str,
        Query(
            min_length=1,
            max_length=200,
            description=(
                "Substring matched against the ontologyTableRow's name "
                "and column headers. Case-insensitive."
            ),
        ),
    ],
    groupBy: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=80,
            description=(
                "Optional grouping column (e.g. 'treatment_group', "
                "'strain'). When unset, all rows form one group "
                "named 'all'."
            ),
        ),
    ] = None,
    groupOrder: Annotated[
        str | None,
        Query(
            max_length=400,
            description=(
                "Optional CSV of group names defining left-to-right "
                "order on the violin plot. Names not present in the "
                "data are dropped; data with unlisted groups appears "
                "after the listed ones."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    group_order_list = (
        [g.strip() for g in groupOrder.split(",") if g.strip()]
        if groupOrder
        else None
    )
    try:
        result = await svc.violin_groups(
            dataset_id,
            variableNameContains,
            group_by=groupBy,
            group_order=group_order_list,
            session=session,
        )
    except (CloudInternalError, CloudUnreachable, CloudTimeout) as exc:
        # Translate cloud-layer failures to a typed 503 envelope —
        # without this, the global FastAPI handler returns an opaque
        # 500 JSON and the chat tool layer can't surface a useful
        # error to the LLM. The frontend `fetchJson` helper maps 503
        # to a clean "Upstream returned 503" message that the LLM
        # then paraphrases. Matches the discipline of /ndi_overview.
        from fastapi.responses import JSONResponse
        log.warning(
            "tabular_query.cloud_error",
            dataset_id=dataset_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "tabular_query unavailable",
                "errorKind": "cloud_unavailable",
                "reason": str(exc) or type(exc).__name__,
            },
        )
    return result
