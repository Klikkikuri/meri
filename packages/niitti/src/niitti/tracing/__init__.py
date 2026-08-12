"""
Tracing subpackage for niitti.

Provides OpenTelemetry initialization, span exporters, crash waterfall diagnostics, and emoji helpers.
"""

import sys
from types import ModuleType
from typing import Any

from niitti.sentry.integration import setup_sentry
from niitti.tracing import crash_buffer, provider
from niitti.tracing.crash_buffer import (
    build_span_forest,
    clear_crash_span_buffer,
    setup_crash_span_dumper,
)
from niitti.tracing.exporters import RingBufferSpanExporter
from niitti.tracing.instrumentation import (
    DEFAULT_SPAN_EMOJI,
    SPAN_EMOJI_MAP,
    get_span_emoji,
    span_id_to_emoji,
)
from niitti.tracing.provider import (
    activate_tracing,
    configure_tracing,
    flush_tracing,
    setup_tracing,
    shutdown_tracing,
)
from niitti.tracing.span import span

__all__ = [
    "DEFAULT_SPAN_EMOJI",
    "SPAN_EMOJI_MAP",
    "RingBufferSpanExporter",
    "activate_tracing",
    "build_span_forest",
    "clear_crash_span_buffer",
    "configure_tracing",
    "crash_buffer",
    "flush_tracing",
    "get_span_emoji",
    "provider",
    "setup_crash_span_dumper",
    "setup_sentry",
    "setup_tracing",
    "shutdown_tracing",
    "span",
    "span_id_to_emoji",
]


class _TracingModule(ModuleType):
    def __getattr__(self, name: str) -> Any:
        if name in ("_active_tracer", "_active_tracer_provider"):
            return getattr(provider, name)
        if name == "_crash_span_exporter":
            return getattr(crash_buffer, name)
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_active_tracer", "_active_tracer_provider"):
            setattr(provider, name, value)
        elif name == "_crash_span_exporter":
            setattr(crash_buffer, name, value)
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _TracingModule
