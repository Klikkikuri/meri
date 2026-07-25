import logging
import structlog
from opentelemetry import baggage, trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from niitti.settings.logging import LoggingSettings

logger = structlog.get_logger(__name__)

BAGGAGE_KEYS = {
    "request_id",
    "tenant_id",
}

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

    # Don't log all baggage unless in debug mode, as it can be verbose and sensitive.

    logging_settings = get_active_logging_settings()
    current_baggage = baggage.get_all()
    if current_baggage and logging_settings and logging_settings.DEBUG:
        event_dict["otel_baggage"] = dict(current_baggage)
    else:
        event_dict["otel_baggage"] = {
            key: value for key, value in current_baggage.items() if key in BAGGAGE_KEYS
        }
    return event_dict


_active_logging_settings: LoggingSettings | None = None


def get_active_logging_settings() -> LoggingSettings | None:
    return _active_logging_settings


def _resolve_log_level(level_name: str, default: int = logging.INFO) -> int:
    """
    Resolve a log level name (e.g. "INFO") to its numeric logging level using
    the standard library's own level registry, instead of a manually
    maintained enumeration that can drift out of sync with `logging`'s actual
    levels (e.g. if a custom level is registered via `logging.addLevelName`).

    :param level_name: Level name, case-insensitive (e.g. "debug", "INFO").
    :param default: Fallback level to use if level_name isn't a known level.
    :return: Numeric logging level.
    """
    name = level_name.upper()

    level_names_mapping = getattr(logging, "getLevelNamesMapping", None)
    if level_names_mapping is not None:
        # Python 3.11+: dedicated, unambiguous name -> level API.
        return level_names_mapping().get(name, default)

    # Older Python: logging.getLevelName() doubles as a name -> level lookup
    # for registered level names. This is long-standing, still-supported
    # stdlib behavior (just superseded by getLevelNamesMapping on 3.11+).
    resolved = logging.getLevelName(name)
    return resolved if isinstance(resolved, int) else default


def setup_logging(settings: LoggingSettings | None = None, force: bool = False) -> None:
    """
    Setup logging for the application using structlog and standard logging.

    By default (force=False), this only takes ownership of the root logger's
    handlers and level if nothing has configured them yet (i.e.
    `logging.getLogger()` has no handlers). If the application has already
    set up its own root logging, niitti leaves those handlers and level
    alone rather than tearing them down - a shared library forcibly
    replacing the caller's root logging configuration is hostile behavior,
    especially for something imported as a dependency rather than run as an
    entrypoint. structlog's own processors are still configured either way,
    so niitti's own structured log calls work regardless.

    Pass force=True to make niitti take over root logging unconditionally
    (clearing any existing handlers and installing its own), matching the
    previous behavior. This is appropriate for standalone entrypoints
    (scripts, workers, `niitti`-owned services) that want full control of the
    process's logging setup, not for use inside a library embedded in
    someone else's application.

    :param settings: LoggingSettings instance. If None, default LoggingSettings() will be initialized.
    :param force: If True, unconditionally replace the root logger's handlers and level.
        If False (default), only do so when the root logger has no handlers configured yet.
    """
    global _active_logging_settings
    if settings is None:
        settings = _active_logging_settings or LoggingSettings()
    _active_logging_settings = settings

    log_level = _resolve_log_level(settings.LOG_LEVEL)

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

    structlog.reset_defaults()
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    try:
        from structlog.dev import rich_traceback

        exception_formatter = rich_traceback
    except ImportError:
        exception_formatter = structlog.dev.plain_traceback

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer(colors=True, exception_formatter=exception_formatter)
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

    root_logger = logging.getLogger()
    root_already_configured = bool(root_logger.handlers)

    if force or not root_already_configured:
        root_logger.handlers.clear()
        root_logger.addHandler(handler)
        root_logger.setLevel(log_level)

        logging.getLogger("haystack").setLevel(log_level)

        if settings.DEBUG:
            root_logger.setLevel(logging.DEBUG)
            logging.getLogger("haystack").setLevel(logging.DEBUG)

        # LoggingInstrumentor injects trace_id/span_id into stdlib log records by
        # adding its own handler to the root logger - this is root logging
        # configuration just like the handler above, so it's gated the same way:
        # only applied when niitti owns root logging (force, or nothing else has
        # configured it yet).
        instrumentor = LoggingInstrumentor()
        if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
            instrumentor.instrument()
    else:
        logger.debug(
            "Root logger already has handlers configured by the application; "
            "leaving its handlers and level untouched, and skipping "
            "LoggingInstrumentor's root handler injection. Pass force=True to "
            "niitti.setup_logging() to override this and take over root logging.",
        )
