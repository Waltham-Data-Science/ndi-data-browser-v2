"""Structured logging setup.

Uses structlog with JSON rendering in production and colorized console in development.
Every log event carries `request_id` and `user_id_hash` if set via context vars.
"""
from __future__ import annotations

import contextvars
import logging
import sys
from typing import Any, cast

import structlog

from ..config import get_settings

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
user_id_hash_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_id_hash", default=None
)


def _add_context(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    req_id = request_id_ctx.get()
    if req_id:
        event_dict["request_id"] = req_id
    uid = user_id_hash_ctx.get()
    if uid:
        event_dict["user_id_hash"] = uid
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.LOG_FORMAT == "json":
        # JSONRenderer needs format_exc_info as a processor to serialize
        # any captured traceback into the JSON payload.
        renderer: Any = structlog.processors.JSONRenderer()
        shared.append(structlog.processors.format_exc_info)
    else:
        # ConsoleRenderer formats exceptions natively using
        # `rich`/`better-exceptions` when available, and emits a
        # UserWarning if `format_exc_info` is also in the chain (which
        # `pytest -W error::UserWarning` then escalates to a test
        # failure). Leave it out for console mode.
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # cache_logger_on_first_use=False (was True before Stream 6.6):
    # caching binds each ``get_logger(__name__)`` lazy-proxy to its
    # first-seen processor chain. In production that's a tiny win,
    # but it breaks pytest's structlog.testing.capture_logs() inside
    # unit tests that run AFTER an integration test has called
    # configure_logging() — the cached proxies stay pinned to the
    # integration chain even when the unit test re-configures. The
    # symptom: capture_logs returns an empty list while pytest's
    # "captured log call" panel shows the WARNING was emitted (caught
    # 2026-05-15 against three test_cloud_client + test_dependencies
    # flakes). Disabling the cache costs ~1-2 µs per log call in prod
    # (negligible vs the network round-trip these logs accompany) and
    # makes the test harness behave deterministically.
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "ndb") -> structlog.stdlib.BoundLogger:
    # structlog.get_logger is typed as Any; narrow to our concrete BoundLogger.
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
