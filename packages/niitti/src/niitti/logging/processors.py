"""
Structlog processors for injecting OpenTelemetry tracing context and baggage.
"""

from typing import Any
from opentelemetry import baggage, trace

BAGGAGE_KEYS = {
    "request_id",
    "tenant_id",
}


def add_opentelemetry_context(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Inject OpenTelemetry trace_id, span_id, and Baggage into structlog event_dict.

    :param logger: Structlog logger instance.
    :param method_name: Method name invoked on logger.
    :param event_dict: Event dictionary being processed.
    :return: Updated event dictionary.
    """
    from niitti.logging.config import get_active_logging_settings

    span = trace.get_current_span()
    if span.is_recording():
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = f"{ctx.trace_id:032x}"
            event_dict["span_id"] = f"{ctx.span_id:016x}"

    # Don't log all baggage unless in debug mode, as it can be verbose and sensitive.
    logging_settings = get_active_logging_settings()
    current_baggage = baggage.get_all()
    if current_baggage and logging_settings and logging_settings.DEBUG:
        event_dict["otel_baggage"] = dict(current_baggage)
    else:
        event_dict["otel_baggage"] = {key: value for key, value in current_baggage.items() if key in BAGGAGE_KEYS}
    return event_dict
