"""Query endpoints — general NDI query and cross-cloud appears-elsewhere."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.facet_service import FacetService, FacetsResponse
from ..services.query_service import QueryRequest, QueryService
from ._deps import facet_service, limit_queries, limit_reads, query_service

router = APIRouter(prefix="/api/query", tags=["query"], dependencies=[Depends(limit_queries)])

# Facets is a read-only aggregation endpoint — bound by limit_reads instead of
# limit_queries. Own router so the rate-limit dependency differs cleanly.
facets_router = APIRouter(prefix="/api/facets", tags=["facets"], dependencies=[Depends(limit_reads)])


@facets_router.get("", response_model=FacetsResponse)
async def facets(
    svc: Annotated[FacetService, Depends(facet_service)],
    # Optional auth — facets aggregate public-dataset data. Authenticated
    # users see the same aggregation (no per-user scoping; ADR-013 §Cache).
    _session: Annotated[SessionData | None, Depends(get_current_session)],
) -> FacetsResponse:
    """Distinct-value facets aggregated across all published datasets.

    Powers the query page filter sidebar: one list each for species /
    brainRegions / strains / sexes / probeTypes. Cached under
    ``facets:v1`` with a 5-minute TTL (amendment §4.B3 freshness budget).
    """
    return await svc.build_facets()


class AppearsElsewhereBody(BaseModel):
    documentId: str = Field(..., min_length=1, max_length=256)
    excludeDatasetId: str | None = None


@router.post("")
async def run(
    body: QueryRequest,
    svc: Annotated[QueryService, Depends(query_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    return await svc.execute(
        body, access_token=session.access_token if session else None,
    )


@router.get("/operations")
async def operations(
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    """Describe the supported ndiquery operations so the frontend builder
    renders the right input widgets per op. Plan §M6 backend step 1.
    """
    return {
        "operations": [
            {
                "name": "isa",
                "label": "is a (type)",
                "description": "Match documents whose class lineage includes this class name.",
                "paramSchema": {"param1": "class name"},
                "negatable": True,
            },
            {
                "name": "depends_on",
                "label": "depends on",
                "description": "Match documents that depend on the given ndiId via edges matching `param1` (or `*`).",
                "paramSchema": {"param1": "edge name or *", "param2": "ndiId or list of ndiIds"},
                "negatable": True,
            },
            {
                "name": "hasfield",
                "label": "field exists",
                "description": "Match documents where `field` exists and is not null.",
                "paramSchema": {"field": "dotted path under data.*"},
                "negatable": True,
            },
            {
                "name": "exact_string",
                "label": "equals (string)",
                "description": "Case-sensitive exact-match string.",
                "paramSchema": {"field": "dotted path", "param1": "value"},
                "negatable": True,
            },
            {
                "name": "exact_string_anycase",
                "label": "equals (case-insensitive)",
                "description": "Case-insensitive exact-match string.",
                "paramSchema": {"field": "dotted path", "param1": "value"},
                "negatable": True,
            },
            {
                "name": "contains_string",
                "label": "contains",
                "description": "Case-insensitive substring match.",
                "paramSchema": {"field": "dotted path", "param1": "substring"},
                "negatable": True,
            },
            {
                "name": "regexp",
                "label": "matches regex",
                "description": "Regular-expression match (case-insensitive).",
                "paramSchema": {"field": "dotted path", "param1": "regex"},
                "negatable": True,
            },
            {
                "name": "exact_number",
                "label": "= (number)",
                "paramSchema": {"field": "dotted path", "param1": "number"},
                "negatable": True,
            },
            {
                "name": "lessthan",
                "label": "< (number)",
                "paramSchema": {"field": "dotted path", "param1": "number"},
                "negatable": True,
            },
            {
                "name": "lessthaneq",
                "label": "<= (number)",
                "paramSchema": {"field": "dotted path", "param1": "number"},
                "negatable": True,
            },
            {
                "name": "greaterthan",
                "label": "> (number)",
                "paramSchema": {"field": "dotted path", "param1": "number"},
                "negatable": True,
            },
            {
                "name": "greaterthaneq",
                "label": ">= (number)",
                "paramSchema": {"field": "dotted path", "param1": "number"},
                "negatable": True,
            },
            {
                "name": "hasmember",
                "label": "has member",
                "description": "Array field contains this value.",
                "paramSchema": {"field": "dotted path", "param1": "value"},
                "negatable": True,
            },
            {
                "name": "or",
                "label": "OR (any of)",
                "description": "Match documents satisfying either sub-tree. Not negatable.",
                "paramSchema": {"param1": "sub-tree", "param2": "sub-tree"},
                "negatable": False,
            },
        ],
    }


@router.post("/appears-elsewhere")
async def appears_elsewhere(
    body: AppearsElsewhereBody,
    svc: Annotated[QueryService, Depends(query_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    results = await svc.appears_elsewhere(
        document_id=body.documentId,
        exclude_dataset_id=body.excludeDatasetId,
        access_token=session.access_token if session else None,
    )
    return {"datasets": results, "totalReferences": sum(int(r.get("count", 0)) for r in results)}
