"""Structured logging via structlog, bridged to the stdlib ``logging`` module.

Why structlog rather than bare ``logging``: every line carries the run-scoped
context (run_id, provider, stage) automatically, and we can emit human-readable
console output in dev and JSON in prod from the same call sites. Library logs
(SQLAlchemy, boto3, ...) still flow through stdlib ``logging`` and are rendered
by the same processor chain, so output is uniform.

Usage::

    from matchbot.logging_setup import configure_logging, get_logger, bind_run
    configure_logging(level="INFO", json_logs=False)
    log = get_logger(__name__)
    with bind_run(run_id="r-123", provider="dlt_ui"):
        log.info("stage.start", stage="parse")
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog


def configure_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        renderer: Any = structlog.processors.JSONRenderer(
            serializer=_orjson_dumps,
        )
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    # Route stdlib logging through structlog's ProcessorFormatter so third-party
    # library logs share the same rendering.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Tame noisy libraries.
    for noisy in ("sqlalchemy.engine", "botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _orjson_dumps(obj: Any, *, default: Any = None) -> str:
    import orjson

    return orjson.dumps(obj, default=default).decode("utf-8")


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


@contextmanager
def bind_run(**kwargs: Any) -> Iterator[None]:
    """Bind run-scoped context (run_id, provider, ...) for the enclosed block.

    Implemented with contextvars so the context is correct across function
    calls within the run and cleaned up on exit.
    """
    tokens = structlog.contextvars.bind_contextvars(**kwargs)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
