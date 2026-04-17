"""Ontology term lookup."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..services.ontology_service import OntologyService
from ._deps import limit_reads, ontology_service

router = APIRouter(prefix="/api/ontology", tags=["ontology"], dependencies=[Depends(limit_reads)])


class BatchBody(BaseModel):
    terms: list[str] = Field(..., min_length=1, max_length=200)


@router.get("/lookup")
async def lookup(
    svc: Annotated[OntologyService, Depends(ontology_service)],
    term: str = Query(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    result = await svc.lookup(term)
    return result.to_dict()


@router.post("/batch-lookup")
async def batch(
    body: BatchBody,
    svc: Annotated[OntologyService, Depends(ontology_service)],
) -> dict[str, Any]:
    results = await svc.batch_lookup(body.terms)
    return {"terms": [r.to_dict() for r in results]}


@router.get("/providers")
async def providers(
    svc: Annotated[OntologyService, Depends(ontology_service)],
) -> dict[str, Any]:
    return {"providers": [{"id": k, "name": v} for k, v in svc.PROVIDERS.items()]}


@router.get("/cache-stats")
async def cache_stats(
    svc: Annotated[OntologyService, Depends(ontology_service)],
) -> dict[str, Any]:
    return svc.stats()
