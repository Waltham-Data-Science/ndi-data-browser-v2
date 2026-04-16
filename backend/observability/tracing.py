"""OpenTelemetry tracing — configured but disabled until M4.

Importing this module does NOT enable tracing. Call `configure_tracing()` explicitly
once the app reaches the observability-full milestone. Keeping the module present
means the rest of the code can `from .tracing import span` without conditional imports.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

_tracer: Any | None = None


def configure_tracing(service_name: str = "ndi-data-browser-v2") -> None:
    """Set up OpenTelemetry SDK. No-op if the optional deps aren't installed."""
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore[import-not-found]
    except ImportError:
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Context manager that opens a tracing span if tracing is configured, else no-op."""
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            try:
                s.set_attribute(k, v)
            except Exception:
                pass
        yield
