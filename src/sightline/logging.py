"""Structured logging, wired up from the first commit.

The failure that killed an earlier local-AI pipeline on this network was **silent**, not loud: a
wrong model name returned 404 and a cold start timed out, for weeks, with nothing in the logs. So
every model call is logged here with its latency and its token counts, and when the server does
not report token counts that absence is logged as a fact (``tokens_reported: false``) rather than
omitted. An absent field looks like a field nobody needed; an explicit ``false`` looks like a
question somebody asked.

Every event dict passes through :func:`sightline.redaction.scrub_object` on the way out, so a
Canvas URL or token that reaches a log call by accident does not reach the log.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, TextIO
from urllib.parse import urlsplit

import structlog

from .redaction import scrub, scrub_object

_configured = False


def _scrub_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Scrub every string in the event, including the message and any exception text."""
    return dict(scrub_object(dict(event_dict)))  # type: ignore[arg-type]


def configure_logging(
    level: str = "INFO",
    fmt: Literal["json", "console"] = "json",
    stream: TextIO | None = None,
) -> None:
    """Configure structlog and the stdlib root logger to emit the same structured stream.

    Safe to call more than once; the second call reconfigures rather than duplicating handlers.
    ``stream`` defaults to stdout and exists so tests can assert on the bytes that are actually
    rendered, rather than on an intercepted record that never passed through the formatter.
    """
    global _configured

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _scrub_processor,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # uvicorn installs its own handlers; drop them so there is one stream, not three.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    _configured = True


def is_configured() -> bool:
    return _configured


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name or "sightline")


def endpoint_host(url: str | None) -> str | None:
    """Host portion of an endpoint, for logging. Still passes through redaction."""
    if not url:
        return None
    parsed = urlsplit(url if "//" in url else f"//{url}")
    return parsed.hostname


@dataclass
class ModelCallRecord:
    """Mutable record filled in by the caller inside a :func:`model_call` block."""

    operation: str
    model: str
    endpoint: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def tokens(
        self,
        *,
        prompt: int | None = None,
        completion: int | None = None,
        total: int | None = None,
    ) -> None:
        """Record token counts as reported by the serving model."""
        if prompt is not None:
            self.prompt_tokens = prompt
        if completion is not None:
            self.completion_tokens = completion
        if total is not None:
            self.total_tokens = total
        elif prompt is not None or completion is not None:
            self.total_tokens = (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    @property
    def tokens_reported(self) -> bool:
        return any(
            value is not None
            for value in (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        )


@contextmanager
def model_call(
    operation: str,
    *,
    model: str,
    endpoint: str | None = None,
    logger: structlog.stdlib.BoundLogger | None = None,
    **extra: Any,
) -> Iterator[ModelCallRecord]:
    """Time a model call and log latency, token counts and outcome — success or failure.

    The block logs exactly once on the way out. A raised exception is logged at ``error`` with a
    scrubbed message and then re-raised; nothing is swallowed.
    """
    log = logger or get_logger("sightline.model")
    record = ModelCallRecord(operation=operation, model=model, endpoint=endpoint)
    record.extra.update(extra)
    started = time.perf_counter()
    try:
        yield record
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        log.error(
            "model_call",
            operation=operation,
            model=model,
            endpoint_host=endpoint_host(endpoint),
            latency_ms=latency_ms,
            status="error",
            error_type=type(exc).__name__,
            error=scrub(str(exc)),
            tokens_reported=record.tokens_reported,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            total_tokens=record.total_tokens,
            **record.extra,
        )
        raise
    else:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "model_call",
            operation=operation,
            model=model,
            endpoint_host=endpoint_host(endpoint),
            latency_ms=latency_ms,
            status="ok",
            tokens_reported=record.tokens_reported,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            total_tokens=record.total_tokens,
            **record.extra,
        )
