"""
Span exporters for OpenTelemetry tracing.
"""

import threading
from collections import deque
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

DEFAULT_CRASH_SPAN_BUFFER_SIZE = 5000


class RingBufferSpanExporter(SpanExporter):
    """
    A SpanExporter that retains only the most recently finished spans, up to a
    fixed capacity, for crash-time diagnostics.

    Unlike opentelemetry's InMemorySpanExporter, this exporter never grows
    unbounded: once `maxlen` spans are stored, older spans are evicted
    automatically as new ones arrive (FIFO eviction via a bounded deque).
    This makes it safe to use in long-running processes (daemons, background
    schedulers) without requiring callers to periodically clear the buffer.

    Thread-safe: export()/get_finished_spans()/clear() all take an internal
    lock, matching the behavior of the standard InMemorySpanExporter.
    """

    def __init__(self, maxlen: int = DEFAULT_CRASH_SPAN_BUFFER_SIZE) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be a positive integer")
        self._maxlen = maxlen
        self._spans: deque[ReadableSpan] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._stopped = False

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def export(self, spans) -> SpanExportResult:
        if self._stopped:
            return SpanExportResult.FAILURE
        with self._lock:
            self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def get_finished_spans(self) -> tuple[ReadableSpan, ...]:
        with self._lock:
            return tuple(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def shutdown(self) -> None:
        self._stopped = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
