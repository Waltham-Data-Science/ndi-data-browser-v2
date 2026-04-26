"""OpenTelemetry tracing gate (O7).

Small surface — pin the contract that ``init_tracing``:
- Returns False (and is silent) when ``OTEL_EXPORTER_OTLP_ENDPOINT``
  is unset.
- Returns False without raising when the opentelemetry deps are not
  importable.
- Returns True when both the env is set and the deps import cleanly
  AND no exporter setup error occurs.

We don't exercise actual span emission here — that requires a live
OTLP collector. The contract this module owns is "the gate decides
correctly," not "spans round-trip end-to-end."
"""
from __future__ import annotations

import importlib.util
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from backend.config import get_settings
from backend.observability.tracing import init_tracing


def _otel_extra_installed() -> bool:
    """Check whether the optional `observability` extra is importable.

    `importlib.util.find_spec` raises ModuleNotFoundError when the
    parent module is itself absent, so we wrap to a clean bool.
    """
    try:
        return importlib.util.find_spec(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        ) is not None
    except ModuleNotFoundError:
        return False


_HAVE_OTEL = _otel_extra_installed()


def test_init_tracing_returns_false_when_endpoint_unset(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No endpoint configured → no tracer set up. Zero overhead path."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    get_settings.cache_clear()
    try:
        app = FastAPI()
        result = init_tracing(app, get_settings())
        assert result is False
    finally:
        get_settings.cache_clear()


def test_init_tracing_returns_false_when_opentelemetry_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Endpoint configured but the opentelemetry packages aren't
    importable (slim install) → graceful degrade, no startup crash."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test:4318")
    get_settings.cache_clear()

    # Simulate ImportError by stubbing the import to raise.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name.startswith("opentelemetry"):
            raise ImportError(f"simulated: {name} not installed")
        return real_import(name, *args, **kwargs)

    try:
        app = FastAPI()
        with patch("builtins.__import__", side_effect=fake_import):
            result = init_tracing(app, get_settings())
        assert result is False
    finally:
        get_settings.cache_clear()


@pytest.mark.skipif(
    not _HAVE_OTEL,
    reason="opentelemetry observability extra not installed in this env",
)
def test_init_tracing_returns_true_when_endpoint_set_and_deps_installed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Endpoint set + deps installed → tracer wired up. We can only
    run this test when the `observability` extra is actually installed
    in the dev env; otherwise skip gracefully."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.test:4318")
    get_settings.cache_clear()
    try:
        app = FastAPI()
        result = init_tracing(app, get_settings())
        # If the import succeeded and the exporter constructor didn't
        # raise (it shouldn't — we're not actually sending spans, the
        # exporter just stores config), we get True.
        assert result is True
    finally:
        get_settings.cache_clear()
