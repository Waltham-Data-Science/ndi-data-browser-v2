"""OpenTelemetry tracing initialization (O7).

Conditional setup for distributed-tracing instrumentation. The
opentelemetry deps live as an *optional* extra in
``backend/pyproject.toml`` (``[project.optional-dependencies].observability``),
so a default install (`pip install -r backend/requirements.txt`) gets
them; a slim install or a developer who hasn't refreshed deps may not.

This module is the one place where opentelemetry is imported at
runtime. The import is wrapped in try/except so an unmet dep
degrades to "tracing disabled" instead of import-error-on-startup.

Tracing is gated on the ``OTEL_EXPORTER_OTLP_ENDPOINT`` setting:
- Empty (default): no tracer is configured. Zero overhead.
- Non-empty (e.g. ``https://otel-collector.internal:4318``): the
  global tracer provider is set; FastAPI + httpx instrumentations
  attach so requests + outbound calls produce spans automatically.

The audit (synthesis §O7) flagged that the deps were declared but
never instantiated. This module closes that gap without forcing the
deps on every install path.
"""
from __future__ import annotations

from fastapi import FastAPI

from ..config import Settings
from .logging import get_logger

log = get_logger(__name__)


def init_tracing(app: FastAPI, settings: Settings) -> bool:
    """Initialize OpenTelemetry instrumentation when configured.

    Returns ``True`` if the tracer was set up (env configured + deps
    installed), ``False`` otherwise. Callers don't need the return
    value; it's exposed for tests to assert on.

    Failure modes that degrade silently to ``False``:
    - ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset/empty.
    - The opentelemetry packages are not importable (slim install).
    - The exporter constructor raises during setup (network DNS
      failure at startup, etc.). Tracing is non-essential — a
      configuration error must NOT prevent the app from starting.
    """
    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.strip()
    if not endpoint:
        log.debug("tracing.disabled", reason="endpoint_not_configured")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        log.warning(
            "tracing.disabled",
            reason="opentelemetry_not_installed",
            error=str(e),
        )
        return False

    try:
        resource = Resource.create({
            "service.name": "ndi-data-browser-v2",
            "service.version": "2.0.0",
            "deployment.environment": settings.ENVIRONMENT,
        })
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)),
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        log.warning("tracing.init_failed", error=str(e))
        return False

    log.info(
        "tracing.enabled",
        endpoint=endpoint,
        environment=settings.ENVIRONMENT,
    )
    return True
