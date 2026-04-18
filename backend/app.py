"""FastAPI app entrypoint.

Wires:
- lifespan: start/stop cloud client, Redis pool, ontology cache
- middleware: request-id → security-headers → metrics → CORS → CSRF
- exception handler: BrowserError → stable JSON shape
- routers: health, auth, datasets, documents, tables, query, binary, ontology, visualize
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
from .middleware.csrf import CsrfMiddleware
from .middleware.metrics import MetricsMiddleware
from .middleware.rate_limit import RateLimiter
from .middleware.request_id import RequestIdMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .observability.logging import configure_logging, get_logger, request_id_ctx
from .routers import auth, binary, datasets, documents, health, ontology, query, tables, visualize
from .services.ontology_cache import OntologyCache
from .services.ontology_service import OntologyService
from .static_files import safe_static_path

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()

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

    log.info("app.startup", environment=settings.ENVIRONMENT)
    try:
        yield
    finally:
        await cloud_client.close()
        await ontology_service.close()
        try:
            await redis.aclose()
        except Exception:
            await redis.close()
        log.info("app.shutdown")


def create_app() -> FastAPI:  # noqa: PLR0915  (single orchestration function, intentional)
    settings = get_settings()
    app = FastAPI(
        title="NDI Data Browser v2",
        version="2.0.0",
        description="Cloud-first proxy + enricher for NDI Cloud.",
        lifespan=lifespan,
    )

    # --- Middleware ---
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
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
    app.include_router(binary.router)
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
