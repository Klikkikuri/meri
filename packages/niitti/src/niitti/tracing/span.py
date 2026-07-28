"""
OpenTelemetry span helper function for niitti.
"""

import functools
from contextlib import AbstractContextManager
from typing import Any, Sequence
import structlog
from opentelemetry import trace


class _SpanContextManager(AbstractContextManager):
    def __init__(
        self,
        name: str,
        log: Any | None = None,
        kind: trace.SpanKind = trace.SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
        links: Sequence[trace.Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        tracer_name: str | None = None,
        **kwargs: Any,
    ):
        self.name = name
        self.log = log
        self.kind = kind
        self.attributes = attributes
        self.links = links
        self.start_time = start_time
        self.record_exception = record_exception
        self.set_status_on_exception = set_status_on_exception
        self.tracer_name = tracer_name
        self.kwargs = kwargs
        self._cm = None
        self._otel_span = None

    def __enter__(self) -> trace.Span:
        merged_attributes = dict(self.attributes) if self.attributes else {}
        if self.kwargs:
            merged_attributes.update(self.kwargs)

        current_ctx = structlog.contextvars.get_contextvars()
        parent_path_str = current_ctx.get("span_path", "")
        full_path_str = f"{parent_path_str}.{self.name}" if parent_path_str else self.name

        tracer_name = self.tracer_name
        if tracer_name is None:
            if self.log is not None and hasattr(self.log, "_logger") and hasattr(self.log._logger, "name"):
                tracer_name = self.log._logger.name
            else:
                tracer_name = "niitti"

        tracer = trace.get_tracer(tracer_name)

        self._bound_cm = structlog.contextvars.bound_contextvars(span_path=full_path_str)
        self._bound_cm.__enter__()

        self._cm = tracer.start_as_current_span(
            name=self.name,
            kind=self.kind,
            attributes=merged_attributes if merged_attributes else None,
            links=self.links,
            start_time=self.start_time,
            record_exception=self.record_exception,
            set_status_on_exception=self.set_status_on_exception,
        )
        self._otel_span = self._cm.__enter__()
        return self._otel_span

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._cm:
                return self._cm.__exit__(exc_type, exc_val, exc_tb)
        finally:
            if hasattr(self, "_bound_cm"):
                self._bound_cm.__exit__(exc_type, exc_val, exc_tb)

    def __call__(self, func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return wrapper


def span(
    name: str,
    log: Any | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
    links: Sequence[trace.Link] | None = None,
    start_time: int | None = None,
    record_exception: bool = True,
    set_status_on_exception: bool = True,
    tracer_name: str | None = None,
    **kwargs: Any,
) -> _SpanContextManager:
    """
    Create an OpenTelemetry span context manager / decorator with automatic span_path tracking.

    :param name: Short, low-cardinality OTel span name (e.g. 'prune_article').
    :param log: Optional bound logger instance (e.g. NiittiBoundLogger).
    :param kind: SpanKind.
    :param attributes: Optional attributes dict.
    :param kwargs: Key-value attributes recorded on the span.
    """
    return _SpanContextManager(
        name=name,
        log=log,
        kind=kind,
        attributes=attributes,
        links=links,
        start_time=start_time,
        record_exception=record_exception,
        set_status_on_exception=set_status_on_exception,
        tracer_name=tracer_name,
        **kwargs,
    )
