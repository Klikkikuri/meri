from niitti.logging import add_opentelemetry_context, setup_logging
from niitti.settings import LoggingSettings, Settings, TracingSettings
from niitti.tracing import (
    DEFAULT_SPAN_EMOJI,
    SPAN_EMOJI_MAP,
    activate_tracing,
    clear_crash_span_buffer,
    configure_tracing,
    flush_tracing,
    get_span_emoji,
    setup_crash_span_dumper,
    setup_sentry,
    setup_tracing,
    shutdown_tracing,
    span_id_to_emoji,
)

__all__ = [
    "Settings",
    "LoggingSettings",
    "TracingSettings",
    "setup_logging",
    "add_opentelemetry_context",
    "configure_tracing",
    "activate_tracing",
    "setup_tracing",
    "flush_tracing",
    "shutdown_tracing",
    "setup_sentry",
    "setup_crash_span_dumper",
    "clear_crash_span_buffer",
    "SPAN_EMOJI_MAP",
    "DEFAULT_SPAN_EMOJI",
    "get_span_emoji",
    "span_id_to_emoji",
]
