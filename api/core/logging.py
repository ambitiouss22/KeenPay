"""Structured logging.

JSON in every environment that is not a developer's terminal, because logs are
read by machines first - grep on a wrapped multi-line traceback finds nothing,
and a field you cannot filter on may as well not be logged. Locally the console
renderer wins, since a human is reading it.

``request_id`` rides along on every line without being passed down through call
signatures: :class:`middleware.middleware.RequestIDMiddleware` binds it to a
contextvar, and ``merge_contextvars`` folds it into each event. That is what
makes "show me everything that happened in this one request" a single query.

This module is the implementation; ``config.logging`` re-exports it so the
existing ``from config.logging import configure_logging`` keeps working. One
implementation, two names, rather than two configurations that drift.
"""

from __future__ import annotations

import logging
import sys

import structlog

from config.settings import get_settings

#: Keys whose values must never reach a log sink.
REDACTED_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "authorization",
        "secret",
        "key_hash",
        "password_hash",
        "signature",
        "jwt_secret",
        "razorpay_key_secret",
        "razorpay_webhook_secret",
    }
)


def _redact(_logger, _name, event_dict):
    """Replace secret-looking values before rendering.

    A log line is the easiest place to leak a credential: someone logs a whole
    request body while debugging, it ships to an aggregator, and now the token
    is in a system with a different access policy. Cheaper to strip centrally
    than to trust every call site.
    """
    for key in list(event_dict):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(*, force_json: bool | None = None) -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if force_json is None:
        # A TTY means a person is watching; anything else is a log collector.
        as_json = settings.is_production or not sys.stdout.isatty()
    else:
        as_json = force_json

    renderer = (
        structlog.processors.JSONRenderer()
        if as_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout, force=True)

    # uvicorn's access log duplicates what LoggingMiddleware already records,
    # in a different format. Two lines per request, one of them unstructured.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


__all__ = ["REDACTED_KEYS", "configure_logging", "get_logger"]
