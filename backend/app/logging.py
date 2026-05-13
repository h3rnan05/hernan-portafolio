"""Structured logging configuration.

structlog gives us JSON logs in production and pretty logs in development.
Call `setup_logging()` once at app startup.
"""

import logging
import sys

import structlog

from app.config import get_settings


def setup_logging() -> None:
    """Configure structlog + stdlib logging."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Stdlib root logger setup
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # structlog setup
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),  # swap for JSONRenderer in prod if desired
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger. Pass __name__ from caller."""
    return structlog.get_logger(name)
