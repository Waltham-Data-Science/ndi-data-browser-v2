"""Prometheus metrics.

Exposed at `/metrics`. Wired from commit 1 per the plan's observability requirements.
"""
from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()

# --- HTTP ---
http_requests_total = Counter(
    "ndb_http_requests_total",
    "Count of HTTP requests by method, route, status",
    ["method", "route", "status"],
    registry=REGISTRY,
)
http_request_duration_seconds = Histogram(
    "ndb_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

# --- Cloud client ---
cloud_call_total = Counter(
    "ndb_cloud_call_total",
    "Count of cloud calls",
    ["endpoint", "outcome"],
    registry=REGISTRY,
)
cloud_call_duration_seconds = Histogram(
    "ndb_cloud_call_duration_seconds",
    "Cloud call latency",
    ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
cloud_retries_total = Counter(
    "ndb_cloud_retries_total",
    "Number of retry attempts by endpoint",
    ["endpoint"],
    registry=REGISTRY,
)
circuit_breaker_state = Gauge(
    "ndb_circuit_breaker_state",
    "1 = closed, 0 = open, 0.5 = half-open",
    registry=REGISTRY,
)

# --- Auth ---
login_attempts_total = Counter(
    "ndb_login_attempts_total",
    "Count of login attempts",
    ["outcome"],
    registry=REGISTRY,
)
cognito_refresh_total = Counter(
    "ndb_cognito_refresh_total",
    "Count of Cognito refresh attempts",
    ["outcome"],
    registry=REGISTRY,
)
cognito_refresh_duration_seconds = Histogram(
    "ndb_cognito_refresh_duration_seconds",
    "Cognito refresh latency",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)
session_count = Gauge(
    "ndb_session_count",
    "Approximate number of active sessions (via Redis dbsize sample)",
    registry=REGISTRY,
)
session_refresh_lock_contention_total = Counter(
    "ndb_session_refresh_lock_contention_total",
    "Times another worker was already refreshing",
    registry=REGISTRY,
)

# --- Tables / queries ---
table_build_duration_seconds = Histogram(
    "ndb_table_build_duration_seconds",
    "Summary table build latency",
    ["class_name"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)
query_execution_duration_seconds = Histogram(
    "ndb_query_execution_duration_seconds",
    "ndiquery call latency",
    ["scope_kind"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
query_timeout_total = Counter(
    "ndb_query_timeout_total",
    "Cloud ndiquery calls that hit Lambda timeout",
    ["scope_kind"],
    registry=REGISTRY,
)

# --- Ontology ---
ontology_cache_hits_total = Counter(
    "ndb_ontology_cache_hits_total",
    "Ontology term cache hits",
    ["provider"],
    registry=REGISTRY,
)
ontology_cache_misses_total = Counter(
    "ndb_ontology_cache_misses_total",
    "Ontology term cache misses",
    ["provider"],
    registry=REGISTRY,
)

# --- Rate limiting ---
rate_limit_rejections_total = Counter(
    "ndb_rate_limit_rejections_total",
    "Requests rejected by rate limiter",
    ["bucket"],
    registry=REGISTRY,
)


def metrics_bytes() -> bytes:
    return generate_latest(REGISTRY)


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
