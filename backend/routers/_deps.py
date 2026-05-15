"""Router-side DI helpers: pull services off app.state."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..auth.session import SessionStore
from ..cache.redis_table import RedisTableCache
from ..clients.ndi_cloud import NdiCloudClient
from ..middleware.rate_limit import Limit, RateLimiter
from ..services.aggregate_documents_service import AggregateDocumentsService
from ..services.binary_service import BinaryService
from ..services.dataset_binding_service import DatasetBindingService
from ..services.dataset_provenance_service import DatasetProvenanceService
from ..services.dataset_service import DatasetService
from ..services.dataset_summary_service import (
    SUMMARY_CACHE_TTL_SECONDS,
    DatasetSummaryService,
)
from ..services.dependency_graph_service import DependencyGraphService
from ..services.document_service import DocumentService
from ..services.facet_service import FacetService
from ..services.image_service import ImageService
from ..services.ontology_service import OntologyService
from ..services.pivot_service import PivotService
from ..services.query_service import QueryService
from ..services.summary_table_service import SummaryTableService
from ..services.tabular_query_service import TabularQueryService
from ..services.visualize_service import VisualizeService


def cloud(request: Request) -> NdiCloudClient:
    return request.app.state.cloud_client  # type: ignore[no-any-return]


def session_store(request: Request) -> SessionStore:
    return request.app.state.session_store  # type: ignore[no-any-return]


def rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


def dataset_service(request: Request) -> DatasetService:
    return DatasetService(cloud(request))


def document_service(request: Request) -> DocumentService:
    return DocumentService(cloud(request))


def query_service(request: Request) -> QueryService:
    return QueryService(cloud(request))


def aggregate_documents_service(request: Request) -> AggregateDocumentsService:
    """Stream 4.9 (2026-05-16) — POST /api/aggregate-documents handler.

    Stateless per-request, mirrors `query_service` shape. The cloud client
    is the only collaborator; nothing held on app.state.
    """
    return AggregateDocumentsService(cloud(request))


def table_cache(request: Request) -> RedisTableCache | None:
    return getattr(request.app.state, "table_cache", None)


def dep_graph_cache(request: Request) -> RedisTableCache | None:
    return getattr(request.app.state, "dep_graph_cache", None)


def summary_table_service(request: Request) -> SummaryTableService:
    return SummaryTableService(cloud(request), cache=table_cache(request))


def tabular_query_service(request: Request) -> TabularQueryService:
    return TabularQueryService(summary_table_service(request))


def dataset_summary_cache(request: Request) -> RedisTableCache | None:
    return getattr(request.app.state, "dataset_summary_cache", None)


def dataset_summary_service(request: Request) -> DatasetSummaryService:
    return DatasetSummaryService(
        cloud(request),
        ontology_service(request),
        cache=dataset_summary_cache(request),
    )


def pivot_cache(request: Request) -> RedisTableCache | None:
    return getattr(request.app.state, "pivot_cache", None)


def pivot_service(request: Request) -> PivotService:
    return PivotService(cloud(request), cache=pivot_cache(request))


# Keep the TTL constant reachable from app.py wiring without a cross-import.
_SUMMARY_CACHE_TTL_SECONDS = SUMMARY_CACHE_TTL_SECONDS


def dependency_graph_service(request: Request) -> DependencyGraphService:
    return DependencyGraphService(cloud(request), cache=dep_graph_cache(request))


def dataset_provenance_cache(request: Request) -> RedisTableCache | None:
    return getattr(request.app.state, "dataset_provenance_cache", None)


def dataset_provenance_service(request: Request) -> DatasetProvenanceService:
    return DatasetProvenanceService(
        cloud(request),
        cache=dataset_provenance_cache(request),
    )


def facets_cache(request: Request) -> RedisTableCache | None:
    return getattr(request.app.state, "facets_cache", None)


def facet_service(request: Request) -> FacetService:
    return FacetService(
        dataset_service(request),
        dataset_summary_service(request),
        cache=facets_cache(request),
    )


def binary_service(request: Request) -> BinaryService:
    return BinaryService(cloud(request))


def image_service(request: Request) -> ImageService:
    return ImageService(cloud(request))


def visualize_service(request: Request) -> VisualizeService:
    return VisualizeService(cloud(request))


def ontology_service(request: Request) -> OntologyService:
    return request.app.state.ontology_service  # type: ignore[no-any-return]


def dataset_binding_service(request: Request) -> DatasetBindingService:
    """Return the singleton DatasetBindingService held on app.state.

    The service owns an in-memory LRU of materialized ndi.dataset.Dataset
    objects + per-id locks for download coalescing — both must persist
    across requests, so this MUST resolve to the shared instance, not a
    new one per call. Lifespan wires
    ``app.state.dataset_binding_service`` at startup.
    """
    return request.app.state.dataset_binding_service  # type: ignore[no-any-return]


# --- Rate-limit helpers ---

async def _subject(
    request: Request,
    store: Annotated[SessionStore, Depends(session_store)],
) -> str:
    """Resolve the rate-limit subject from a validated session, falling back
    to the hashed client IP.

    Critical: we MUST look up the session cookie in Redis before using its
    value, otherwise attackers can defeat per-user rate limits by rotating
    the cookie (each fake value gets a fresh bucket).

    NOTE: paid twice per request today (here + in get_current_session). If
    p99 latency matters, pass the resolved session via request.state.session.
    """
    sid = request.cookies.get("session")
    if sid:
        sess = await store.get(sid)
        if sess is not None:
            return f"u:{sess.user_id}"
    ip = request.client.host if request.client else "unknown"
    return RateLimiter.subject_for(None, ip)


async def limit_reads(
    request: Request,
    subject: Annotated[str, Depends(_subject)],
) -> None:
    from ..config import get_settings
    s = get_settings()
    limiter = rate_limiter(request)
    await limiter.check(
        Limit(bucket="reads", max_requests=s.RATE_LIMIT_READS_PER_MIN, window_seconds=60),
        subject,
    )


async def limit_queries(
    request: Request,
    subject: Annotated[str, Depends(_subject)],
) -> None:
    from ..config import get_settings
    s = get_settings()
    limiter = rate_limiter(request)
    await limiter.check(
        Limit(bucket="query", max_requests=s.RATE_LIMIT_QUERY_PER_MIN, window_seconds=60),
        subject,
    )


async def limit_bulk_fetch(
    request: Request,
    subject: Annotated[str, Depends(_subject)],
) -> None:
    from ..config import get_settings
    s = get_settings()
    limiter = rate_limiter(request)
    await limiter.check(
        Limit(bucket="bulk-fetch", max_requests=s.RATE_LIMIT_BULK_FETCH_PER_MIN, window_seconds=60),
        subject,
    )
