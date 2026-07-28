"""
Logging configuration setup for niitti applications.
"""

import logging
import structlog
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from typing import Any

from niitti.logging.formatters import _resolve_log_level
from niitti.logging.processors import add_opentelemetry_context, filter_full_otel_ids_for_console
from niitti.settings.logging import LoggingSettings


class NiittiBoundLogger(structlog.stdlib.BoundLogger):
    """
    Bound logger for Niitti providing structured logging and integrated OpenTelemetry span creation.
    """

    def span(self, name: str, **attrs: Any):
        """
        Create an OpenTelemetry span context manager associated with this logger's module name.
        """
        from niitti.tracing.span import span as _span

        return _span(name, log=self, **attrs)


def get_logger(*args: Any, **initial_values: Any) -> NiittiBoundLogger:
    """
    Get a structlog NiittiBoundLogger instance.

    :param args: Logger name or module arguments passed to structlog.get_logger.
    :param initial_values: Key-value initial context bound to logger instance.
    :return: NiittiBoundLogger instance.
    """
    return structlog.get_logger(*args, **initial_values)  # type: ignore


logger = structlog.get_logger(__name__)

_active_logging_settings: LoggingSettings | None = None


def get_active_logging_settings() -> LoggingSettings | None:
    """
    Get the currently active LoggingSettings configuration instance.

    :return: Currently active LoggingSettings or None if not configured.
    """
    return _active_logging_settings


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
        wrapper_class=NiittiBoundLogger,
        cache_logger_on_first_use=False,
    )

    try:
        from structlog.dev import rich_traceback

        exception_formatter = rich_traceback
    except ImportError:
        exception_formatter = structlog.dev.plain_traceback

    console_processors = [] if settings.LOG_FORMAT == "json" else [filter_full_otel_ids_for_console]
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer(colors=True, exception_formatter=exception_formatter)
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *console_processors,
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
