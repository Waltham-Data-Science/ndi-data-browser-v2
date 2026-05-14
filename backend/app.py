"""FastAPI app entrypoint.

Wires:
- lifespan: start/stop cloud client, Redis pool, ontology cache
- middleware: request-id → security-headers → metrics → CORS → CSRF
- exception handler: BrowserError → stable JSON shape
- routers: health, auth, datasets, documents, tables, query, binary, signal, ontology, visualize
- static: serves frontend build from ./frontend_dist if present
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth.session import SessionStore
from .cache.redis_table import RedisTableCache
from .clients.ndi_cloud import NdiCloudClient
from .config import get_settings
from .errors import BrowserError, Internal, NotFound, ValidationFailed
from .middleware.cache_control import CacheControlMiddleware
from .middleware.csrf import CsrfMiddleware
from .middleware.metrics import MetricsMiddleware
from .middleware.origin_enforcement import OriginEnforcementMiddleware
from .middleware.rate_limit import RateLimiter
from .middleware.request_id import RequestIdMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .observability.logging import configure_logging, get_logger, request_id_ctx
from .observability.tracing import init_tracing
from .routers import (
    auth,
    binary,
    datasets,
    documents,
    health,
    image,
    ndi_dataset,
    ontology,
    query,
    signal,
    tables,
    tabular_query,
    treatment_timeline,
    visualize,
)
from .services.dataset_binding_service import DatasetBindingService
from .services.ontology_cache import OntologyCache
from .services.ontology_service import OntologyService
from .static_files import safe_static_path

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: PLR0915  (single-function lifespan orchestrator; wiring N typed caches + services is intentional)
    configure_logging()
    settings = get_settings()
    # O7: opt-in OpenTelemetry tracing. No-op when
    # OTEL_EXPORTER_OTLP_ENDPOINT is unset or the observability extra
    # isn't installed.
    init_tracing(app, settings)

    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    app.state.redis = redis

    session_store = SessionStore(redis=redis, settings=settings)
    app.state.session_store = session_store

    cloud_client = NdiCloudClient(settings=settings)
    await cloud_client.start()
    app.state.cloud_client = cloud_client

    limiter = RateLimiter(redis=redis)
    app.state.rate_limiter = limiter

    ontology_cache = OntologyCache()
    ontology_service = OntologyService(ontology_cache)
    app.state.ontology_service = ontology_service

    # Ontology cache warmup — plan §M7 step 7. Async-prefetch ~25 high-
    # frequency terms (NCBITaxon mouse/rat/C.elegans, PATO sex, UBERON
    # V1/hippocampus, CL pyramidal, WBStrain N2…) so the first table
    # render in a fresh deploy doesn't stall on cold ontology lookups.
    import asyncio as _asyncio
    import json as _json
    from pathlib import Path as _Path
    warmup_path = _Path(__file__).parent / "data" / "ontology_warmup.json"
    if warmup_path.exists():
        try:
            warmup = _json.loads(warmup_path.read_text())
            terms = [t for t in warmup.get("terms", []) if isinstance(t, str)]
            if terms:
                log.info("ontology.warmup_start", count=len(terms))

                async def _warmup() -> None:
                    try:
                        await ontology_service.batch_lookup(terms)
                        log.info("ontology.warmup_done", count=len(terms))
                    except Exception as e:
                        log.warning("ontology.warmup_failed", error=str(e))

                # Fire-and-forget; don't block startup on external HTTP.
                # Task reference stored on app.state so asyncio doesn't GC it
                # mid-flight (per RUF006).
                app.state.ontology_warmup_task = _asyncio.create_task(_warmup())
        except Exception as e:
            log.warning("ontology.warmup_config_failed", error=str(e))

    # Redis-backed summary-table response cache (1-hour TTL).
    # Shared across replicas so table builds amortize. Plan §M4a step 3.
    app.state.table_cache = RedisTableCache(redis=redis)

    # Separate cache for dependency graphs, 10-minute TTL per plan §M5.
    # Same Redis connection, different TTL so graph invalidation propagates
    # faster than table invalidation.
    from .services.dependency_graph_service import DEP_GRAPH_TTL_SECONDS
    app.state.dep_graph_cache = RedisTableCache(
        redis=redis, ttl_seconds=DEP_GRAPH_TTL_SECONDS,
    )

    # DatasetSummary synthesizer cache, 5-minute TTL per amendment §4.B3
    # (freshness > TTL economy). Separate cache from tables so a table
    # schema bump doesn't invalidate summaries and vice versa.
    from .services.dataset_summary_service import SUMMARY_CACHE_TTL_SECONDS
    app.state.dataset_summary_cache = RedisTableCache(
        redis=redis, ttl_seconds=SUMMARY_CACHE_TTL_SECONDS,
    )

    # DatasetProvenance aggregator cache (Plan B B5), 5-minute TTL matching
    # the summary cache freshness budget. Separate bucket so a provenance
    # schema bump cannot invalidate summaries.
    from .services.dataset_provenance_service import PROVENANCE_CACHE_TTL_SECONDS
    app.state.dataset_provenance_cache = RedisTableCache(
        redis=redis, ttl_seconds=PROVENANCE_CACHE_TTL_SECONDS,
    )

    # Grain-selectable pivot cache (Plan B B6e), 5-minute TTL with its own
    # `pivot:v1` prefix so a summary schema bump doesn't invalidate pivots
    # and vice versa.
    from .services.pivot_service import PIVOT_CACHE_TTL_SECONDS
    app.state.pivot_cache = RedisTableCache(
        redis=redis, ttl_seconds=PIVOT_CACHE_TTL_SECONDS,
    )

    # Cross-dataset facet aggregator cache (Plan B B3), 5-minute TTL per
    # amendment §4.B3 (freshness > TTL economy). Separate bucket so a facet
    # schema bump doesn't invalidate summaries/provenance and vice versa.
    from .services.facet_service import FACETS_CACHE_TTL_SECONDS
    app.state.facets_cache = RedisTableCache(
        redis=redis, ttl_seconds=FACETS_CACHE_TTL_SECONDS,
    )

    # Upstream keep-warm — pokes ndi-cloud-node on AWS Lambda every 4
    # minutes so the Node/Mongoose container stays hot. Without this a
    # user's first page visit eats the full cold-start (~6-10s) → the
    # SPA shows empty skeletons for that long and users think nothing
    # loaded. A lightweight `GET /datasets/published?pageSize=1` keeps
    # the Lambda execution environment resident. Fire-and-forget; we
    # swallow all errors because the pinger is a performance nicety,
    # not a correctness requirement.
    async def _keep_warm() -> None:
        # AWS Lambda keeps execution environments warm for ~5-15 min of
        # idleness. 4 min stays well inside that window without wasting
        # Lambda invocations during active periods (when traffic keeps
        # it warm for free).
        interval_seconds = 240
        consecutive_failures = 0
        while True:
            try:
                await cloud_client._request(
                    "GET",
                    "/datasets/published",
                    endpoint_label="keepwarm",
                    params={"page": 1, "pageSize": 1},
                )
                if consecutive_failures >= 3:
                    # Recovery after a sustained outage is worth noting.
                    log.warning("keepwarm.recovered", after_failures=consecutive_failures)
                consecutive_failures = 0
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                # Transient hiccups are expected (breaker open during
                # upstream outage, etc.). Escalate to WARNING after 3
                # consecutive misses so a sustained outage surfaces in
                # logs instead of silently ticking along at DEBUG.
                consecutive_failures += 1
                if consecutive_failures <= 2:
                    log.debug("keepwarm.skip", error=str(e))
                else:
                    log.warning(
                        "keepwarm.sustained_failure",
                        count=consecutive_failures,
                        error=str(e),
                    )
            await _asyncio.sleep(interval_seconds)

    # Facet-cache warmer — audit 2026-04-23 (#61). Previously the first
    # user request after every 5-minute TTL expiry paid a ~300-cloud-call
    # facet build. Now we build it server-side every 4 minutes so the
    # user-visible request is always a cache hit. Fire-and-forget;
    # failures swallow (same contract as keepwarm).
    async def _facets_warm() -> None:
        from .services.dataset_service import DatasetService
        from .services.dataset_summary_service import DatasetSummaryService
        from .services.facet_service import FacetService

        # Re-read the lifespan caches each iteration via app.state so a
        # Redis reconnect doesn't leave us with a stale RedisTableCache
        # reference.
        interval_seconds = 240  # 4 min, same cadence as keep-warm
        consecutive_failures = 0
        while True:
            try:
                dataset_svc = DatasetService(cloud_client)
                summary_svc = DatasetSummaryService(
                    cloud_client,
                    ontology_service,
                    cache=app.state.dataset_summary_cache,
                )
                svc = FacetService(
                    dataset_svc,
                    summary_svc,
                    cache=app.state.facets_cache,
                )
                await svc.build_facets()
                if consecutive_failures >= 3:
                    log.warning(
                        "facets_warm.recovered",
                        after_failures=consecutive_failures,
                    )
                consecutive_failures = 0
            except _asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures <= 2:
                    log.debug("facets_warm.skip", error=str(e))
                else:
                    log.warning(
                        "facets_warm.sustained_failure",
                        count=consecutive_failures,
                        error=str(e),
                    )
            await _asyncio.sleep(interval_seconds)

    if settings.ENVIRONMENT == "production":
        # Only worth the tiny ongoing cost in prod — dev/test don't
        # suffer the Lambda cold-start problem.
        app.state.keepwarm_task = _asyncio.create_task(_keep_warm())
        app.state.facets_warm_task = _asyncio.create_task(_facets_warm())
        log.info("keepwarm.started", interval_seconds=240)
        log.info("facets_warm.started", interval_seconds=240)

    # NDI-python strict-boot check.
    #
    # The Phase A integration adds vlt (VHSB), ndicompress, and
    # ndi.ontology. When `NDI_PYTHON_REQUIRED=1` (set by the Railway
    # Dockerfile), the stack MUST be importable or we hard-fail.
    # Unset (dev/test/CI), we log a warning if NDI is missing but
    # keep going — every NDI-python call gracefully returns None and
    # callers fall through to their legacy paths.
    #
    # Why an explicit env var rather than guessing from
    # `settings.ENVIRONMENT`: the test/CI/local matrix is fuzzy, and
    # the only thing that actually matters here is "is this image
    # supposed to have NDI-python installed?" The Dockerfile knows;
    # nothing else needs to.
    import os as _os
    if _os.environ.get("NDI_PYTHON_REQUIRED", "").strip() in ("1", "true", "yes"):
        from .services import ndi_python_service as _ndi
        if not _ndi.is_ndi_available():
            raise RuntimeError(
                "ndi_python_service.is_ndi_available() returned False at "
                "startup but NDI_PYTHON_REQUIRED=1. The NDI-python stack "
                "(vlt, ndicompress, ndi.ontology) failed to import. Check "
                "the Dockerfile's pinned git SHAs and the install layer logs."
            )
        log.info("ndi_python.boot_ok")

    # Sprint 1.5 dataset-binding service — singleton, lives on app.state.
    # Always instantiated (cheap object — empty LRU). The router behind
    # ``/api/datasets/{id}/ndi_overview`` calls into it; on internal
    # failure (NDI-python missing, cloud unreachable, etc.) the service
    # returns None and the router maps that to a 503. Frontend tool
    # falls back to ndi_query gracefully.
    app.state.dataset_binding_service = DatasetBindingService()

    # Optional pre-warm of the 3 demo datasets. We fire-and-forget per
    # dataset so a single failure doesn't block the others. Each task
    # is parked on app.state so asyncio doesn't GC the reference
    # mid-flight (RUF006). We DO NOT await them — they run in the
    # background while the app starts serving requests immediately.
    #
    # If NDI-python isn't available, the service returns None on the
    # first call and we skip the rest — costs essentially nothing.
    async def _prewarm_dataset(dataset_id: str) -> None:
        try:
            log.info("dataset_binding.prewarm_start", dataset_id=dataset_id)
            result = await app.state.dataset_binding_service.get_dataset(
                dataset_id
            )
            if result is not None:
                log.info(
                    "dataset_binding.prewarm_done",
                    dataset_id=dataset_id,
                )
            else:
                # Service already logged the reason at WARN — keep this
                # at INFO so the boot timeline is one-line-per-dataset.
                log.info(
                    "dataset_binding.prewarm_skipped",
                    dataset_id=dataset_id,
                )
        except _asyncio.CancelledError:
            raise
        except Exception as exc:
            # Truly defensive: get_dataset() is documented to never
            # raise, but log loudly if that contract breaks so we know.
            log.warning(
                "dataset_binding.prewarm_unexpected_raise",
                dataset_id=dataset_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # Three demo datasets surfaced by the experimental /ask chat:
    # Dabrowska BNST (EPM behavior), Bhar (chemotaxis), Haley
    # (patch-encounter). Order does not matter; tasks run concurrently.
    # Pre-warm is gated to production-like environments so dev/test
    # boots stay fast.
    if settings.ENVIRONMENT in ("production", "preview"):
        prewarm_ids = (
            "67f723d574f5f79c6062389d",  # Dabrowska BNST
            "69bc5ca11d547b1f6d083761",  # Bhar
            "682e7772cdf3f24938176fac",  # Haley
        )
        app.state.dataset_binding_prewarm_tasks = [
            _asyncio.create_task(_prewarm_dataset(did))
            for did in prewarm_ids
        ]
        log.info(
            "dataset_binding.prewarm_started",
            count=len(prewarm_ids),
        )

    log.info("app.startup", environment=settings.ENVIRONMENT)
    try:
        yield
    finally:
        # Cancel background loops before tearing down the cloud client
        # so we don't race a request-in-flight against
        # `cloud_client.close` or `ontology_service.close`.
        import contextlib as _contextlib
        for task_name in ("keepwarm_task", "facets_warm_task", "ontology_warmup_task"):
            task = getattr(app.state, task_name, None)
            if task is not None:
                task.cancel()
                # Await-after-cancel expects CancelledError; any *other*
                # exception from the loop (e.g. a crash) must propagate
                # so it surfaces in logs instead of disappearing.
                with _contextlib.suppress(_asyncio.CancelledError):
                    await task
        # Cancel any in-flight dataset-binding pre-warm tasks.
        # downloadDataset is blocking I/O inside asyncio.to_thread — we
        # can't actually interrupt it mid-thread, but cancellation
        # prevents the post-download cache-write from running after
        # teardown.
        prewarm_tasks = getattr(app.state, "dataset_binding_prewarm_tasks", None) or []
        for task in prewarm_tasks:
            task.cancel()
            with _contextlib.suppress(_asyncio.CancelledError, Exception):
                await task
        await cloud_client.close()
        await ontology_service.close()
        # `redis.asyncio.Redis.aclose()` is the correct async-context
        # close. The previous fallback to `redis.close()` was broken:
        # in `redis-py>=5.x` the sync `Redis.close()` is a no-op alias
        # retained for backward-compat that returns a coroutine WITHOUT
        # awaiting it, leaking the underlying connection pool at
        # shutdown. Log + move on if aclose raises; do NOT call the
        # sync `.close()` as a fallback.
        try:
            await redis.aclose()
        except Exception as exc:
            log.warning("app.shutdown.redis_close_failed", error=str(exc))
        log.info("app.shutdown")


def create_app() -> FastAPI:  # noqa: PLR0915  (single orchestration function, intentional)
    settings = get_settings()
    # B7 — hide the live OpenAPI spec, Swagger UI, and ReDoc in production.
    # Publishing them hands an attacker a free map of every route, every
    # Pydantic body shape, and every error envelope. In dev/staging the
    # docs are useful for contributors and integration debugging; only
    # `production` flips them off. Tests pin this contract in
    # `backend/tests/unit/test_swagger_lockdown.py`.
    is_production = settings.ENVIRONMENT == "production"
    app = FastAPI(
        title="NDI Data Browser v2",
        version="2.0.0",
        description="Cloud-first proxy + enricher for NDI Cloud.",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    # --- Middleware ---
    # Order matters — Starlette applies in reverse of add order, so
    # the last-added middleware wraps closest to the route handler
    # and the first-added wraps the whole stack.
    #
    # Outermost (first-added): MetricsMiddleware — measures
    # end-to-end latency including every other middleware.
    # Then on the request path:
    #   MetricsMiddleware
    #   → SecurityHeaders
    #   → RequestId
    #   → CORS
    #   → CacheControl
    #   → OriginEnforcement   ← rejects non-allowlisted Origin first
    #   → CsrfMiddleware       ← then double-submit CSRF token check
    #   → handler
    #
    # OriginEnforcement is added BEFORE CsrfMiddleware so it wraps
    # outer relative to CSRF (= runs FIRST in the request flow). A
    # non-allowlisted Origin gets the typed FORBIDDEN reject before
    # the CSRF check fires, which keeps the error codes informative
    # (origin failure surfaces distinctly from CSRF token failure).
    #
    # CacheControl runs AFTER CSRF so the response body's ETag is
    # computed over the final payload. It runs BEFORE SecurityHeaders
    # so the 304-response path still gets security headers added on
    # the way back out.
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "ETag"],
    )
    # Conditional-GET caching for GET /api/* responses. Computes an
    # ETag over the response body, handles If-None-Match for 304s,
    # and adds Cache-Control: private|public depending on whether a
    # session cookie is present.
    app.add_middleware(CacheControlMiddleware)
    # OriginEnforcement runs BEFORE CSRF in the request flow (i.e.,
    # added before CsrfMiddleware so it's outer). A non-allowlisted
    # Origin gets rejected with FORBIDDEN before the CSRF check even
    # runs — keeps the typed reject codes meaningful (origin failure
    # vs. CSRF failure).
    app.add_middleware(OriginEnforcementMiddleware)
    # CSRF last (outermost invocation is first so we want CSRF nearest the app).
    app.add_middleware(CsrfMiddleware)

    # --- Exception handlers ---
    @app.exception_handler(BrowserError)
    async def handle_browser_error(request: Request, exc: BrowserError) -> JSONResponse:
        rid = request_id_ctx.get()
        if exc.http_status >= 500:
            log.error("browser_error", code=exc.code.value, **exc.log_context)
        else:
            log.info("browser_error", code=exc.code.value, **exc.log_context)
        return JSONResponse(status_code=exc.http_status, content=exc.to_response(rid))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = request_id_ctx.get()
        # Sanitize: Pydantic includes `ctx.error` which is a raw Exception object —
        # not JSON-serializable and not safe to surface.
        clean_errors: list[dict[str, object]] = []
        for e in exc.errors()[:20]:
            loc = list(e.get("loc", []))
            msg = str(e.get("msg", ""))
            typ = str(e.get("type", ""))
            inp = e.get("input")
            # Only include primitives for `input`.
            if not isinstance(inp, (str, int, float, bool, type(None))):
                inp = None
            clean_errors.append({"loc": loc, "msg": msg, "type": typ, "input": inp})
        # Check for our ~or guard: surface as a specific code.
        if any("~or" in str(e.get("msg", "")) for e in exc.errors()):
            from .errors import QueryInvalidNegation
            err_qn = QueryInvalidNegation()
            return JSONResponse(status_code=err_qn.http_status, content=err_qn.to_response(rid))
        err = ValidationFailed(
            "Request validation failed.",
            details={"errors": clean_errors},
        )
        return JSONResponse(status_code=err.http_status, content=err.to_response(rid))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = request_id_ctx.get()
        if exc.status_code == 404:
            err: BrowserError = NotFound(str(exc.detail) if exc.detail else None)
        elif exc.status_code == 400:
            err = ValidationFailed(str(exc.detail) if exc.detail else "Bad request.")
        else:
            err = Internal(str(exc.detail) if exc.detail else None)
            err.http_status = exc.status_code
        return JSONResponse(status_code=err.http_status, content=err.to_response(rid))

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id_ctx.get()
        log.exception("unhandled_exception", error=str(exc))
        err = Internal()
        return JSONResponse(status_code=err.http_status, content=err.to_response(rid))

    # --- Routers ---
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(datasets.router)
    app.include_router(documents.router)
    app.include_router(tables.router)
    app.include_router(query.router)
    app.include_router(query.facets_router)
    app.include_router(binary.router)
    app.include_router(signal.router)
    app.include_router(image.router)
    app.include_router(tabular_query.router)
    app.include_router(treatment_timeline.router)
    app.include_router(ndi_dataset.router)
    app.include_router(ontology.router)
    app.include_router(visualize.router)

    # --- Static frontend ---
    dist = Path(__file__).resolve().parent.parent / "frontend_dist"
    if dist.is_dir():
        # Serve built assets (hashed filenames in /assets/*) directly.
        assets_dir = dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # Other root-level static files (favicon, robots.txt, etc.)
        index_path = dist / "index.html"

        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """Serve index.html for all non-API client routes so React Router can handle them.

            Paths starting with `api/` or `metrics` are not reached because the routers
            are registered before this catch-all. Known static files at the root
            (favicon.ico, robots.txt, etc.) are served if they exist on disk.

            Path traversal attempts (``../`` / decoded ``%2e%2e%2f``) are rejected
            by :func:`safe_static_path`'s containment check; traversal requests
            fall through to ``index.html`` so the React Router client can render
            its own not-found state.
            """
            target = safe_static_path(dist, full_path)
            if target is not None:
                return FileResponse(target)
            return FileResponse(index_path)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
