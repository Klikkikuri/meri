"""
Structlog processors for injecting OpenTelemetry tracing context and baggage.
"""

from typing import Any
from opentelemetry import baggage, trace
from structlog.types import EventDict

BAGGAGE_KEYS = {
    "request_id",
    "tenant_id",
}


def add_opentelemetry_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Inject OpenTelemetry trace_id, span_id, and Baggage into structlog event_dict.

    Shortens trace_id and span_id to 8 characters for concise display while
    storing full IDs in trace_id_full and span_id_full. Removes otel_baggage
    when empty.

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
            trace_id_full = f"{ctx.trace_id:032x}"
            span_id_full = f"{ctx.span_id:016x}"
            event_dict["trace_id"] = trace_id_full[:8]
            event_dict["span_id"] = span_id_full[:8]
            event_dict["trace_id_full"] = trace_id_full
            event_dict["span_id_full"] = span_id_full

    # Don't log all baggage unless in debug mode, as it can be verbose and sensitive.
    logging_settings = get_active_logging_settings()
    current_baggage = baggage.get_all()
    baggage_dict = (
        dict(current_baggage)
        if current_baggage and logging_settings and logging_settings.DEBUG
        else {key: value for key, value in current_baggage.items() if key in BAGGAGE_KEYS}
    )
    if baggage_dict:
        event_dict["otel_baggage"] = baggage_dict
    else:
        event_dict.pop("otel_baggage", None)
    return event_dict


def filter_full_otel_ids_for_console(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Remove full OpenTelemetry trace and span IDs for console log output.

    :param logger: Structlog logger instance.
    :param method_name: Method name invoked on logger.
    :param event_dict: Event dictionary being processed.
    :return: Updated event dictionary.
    """
    event_dict.pop("trace_id_full", None)
    event_dict.pop("span_id_full", None)
    return event_dict
