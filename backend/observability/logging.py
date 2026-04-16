"""Structured logging setup.

Uses structlog with JSON rendering in production and colorized console in development.
Every log event carries `request_id` and `user_id_hash` if set via context vars.
"""
from __future__ import annotations

import contextvars
import logging
import sys
from typing import Any

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
        structlog.processors.format_exc_info,
    ]

    if settings.LOG_FORMAT == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "ndb") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
