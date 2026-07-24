import logging
import structlog
from opentelemetry import baggage, trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from niitti.settings.logging import LoggingSettings

logger = structlog.get_logger(__name__)


def add_opentelemetry_context(logger, method_name, event_dict):
    """
    Inject OpenTelemetry trace_id, span_id, and Baggage into structlog event_dict.
    """
    span = trace.get_current_span()
    if span.is_recording():
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = f"{ctx.trace_id:032x}"
            event_dict["span_id"] = f"{ctx.span_id:016x}"

    current_baggage = baggage.get_all()
    if current_baggage:
        event_dict["otel_baggage"] = dict(current_baggage)

    return event_dict


def setup_logging(settings: LoggingSettings | None = None) -> None:
    """
    Setup logging for the application using structlog and standard logging.

    :param settings: LoggingSettings instance. If None, default LoggingSettings() will be initialized.
    """
    if settings is None:
        settings = LoggingSettings()

    match settings.LOG_LEVEL.upper():
        case "DEBUG":
            log_level = logging.DEBUG
        case "INFO":
            log_level = logging.INFO
        case "WARNING":
            log_level = logging.WARNING
        case "ERROR":
            log_level = logging.ERROR
        case "CRITICAL":
            log_level = logging.CRITICAL
        case _:
            log_level = logging.INFO

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        add_opentelemetry_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    logging.basicConfig(
        handlers=[handler],
        level=log_level,
        force=True,
    )

    logging.getLogger("haystack").setLevel(log_level)
    LoggingInstrumentor().instrument()

    if settings.DEBUG:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("haystack").setLevel(logging.DEBUG)
