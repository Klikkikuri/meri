import logging
import os
from importlib import import_module
from importlib.metadata import metadata

import structlog
from langdetect import detect
from langdetect.detector import Detector
from opentelemetry import trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
    get_aggregated_resources,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from url_normalize import url_normalize

from .exceptions import UnknownLanguageException

logger = structlog.get_logger(__name__)

EXTRA_RESOURCE_DETECTOR = [
    ("opentelemetry.resource.detector.container", "ContainerResourceDetector")
]
""" List of extra resource detectors to use, if available. """

EXTRA_INSTRUMENTOR = [
    ("opentelemetry.instrumentation.system_metrics", "SystemMetricsInstrumentor"),
    # ("opentelemetry.instrumentation.logging", "LoggingInstrumentor"),
    # ("opentelemetry.instrumentation.asyncio", "AsyncioInstrumentor"),
    ("opentelemetry.instrumentation.urllib3", "URLLib3Instrumentor"),
    ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ("opentelemetry.instrumentation.jinja2", "Jinja2Instrumentor"),
    ("opentelemetry.instrumentation.openai_v2", "OpenAIInstrumentor"),
    ("opentelemetry.instrumentation.click", "ClickInstrumentor"),
    ("opentelemetry.instrumentation.threading", "ThreadingInstrumentor"),
    # SQLAlchemyInstrumentor is not included here, it's included in the `get_db` function
]
""" List of extra instrumentors to use, if available. """


def detect_language(body: str) -> str:
    """
    Detect the language of the text from text body.

    This function uses the langdetect library to detect the language of the given text.
    Raises :class:`UnknownLanguageException` if the language could not be detected.

    :param body: The text body to detect the language from.
    :return: The detected language code.
    :raises LangDetectException: Error in langdetect library.
    :raises UnknownLanguageException: Language could not be detected.
    """
    content_lang = detect(body)

    # Fail if the language could not be detected
    if content_lang == Detector.UNKNOWN_LANG:
        logger.error("Could not detect language")
        raise UnknownLanguageException("Could not detect language")

    logger.debug("Detected language %r", content_lang)

    # Normalize the language code
    content_lang, *_ = content_lang.lower().split("-")
    return content_lang


def clean_url(url: str) -> str:
    """
    Clean the URL to a normalized form.

    ..todo:: Implement common URL cleaning methods for Paatti and Meri.

    :param url: URL to clean
    """
    return url_normalize(url)


def setup_logging(debug=None):
    """
    Setup logging for the application.

    Configures structlog and standard logging.
    """

    from .settings import settings

    # Determine root log level
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

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Use basicConfig with force=True to simplify handler management (Python 3.8+)
    logging.basicConfig(
        handlers=[handler],
        level=log_level,
        force=True
    )

    logging.getLogger("haystack").setLevel(log_level)

    LoggingInstrumentor().instrument()

    # Set the top-level module to DEBUG if debug is True
    if debug is None:
        debug = os.getenv("DEBUG", "0") == "1"

    if debug:
        logging.getLogger(__package__).setLevel(logging.DEBUG)
        return


def setup_sentry():
    import sentry_sdk
    from .settings import settings

    if not settings.sentry.dsn:
        logger.debug("Sentry DSN not set, skipping Sentry initialization")
        return

    integrations = []
    if not settings.sentry.send_logs:
        try:
            from sentry_sdk.integrations.logging import LoggingIntegration
            integrations.append(LoggingIntegration(event_level=None, level=None))
        except ImportError:
            pass

    if settings.sentry.openai_integration:
        try:
            from sentry_sdk.integrations.openai import OpenAIIntegration
            integrations.append(OpenAIIntegration())
        except ImportError:
            logger.debug("OpenAIIntegration not available")

    # Note: OpenTelemetry integration is handled in setup_tracing to avoid
    # conflicts with custom TracerProvider setup.

    sentry_sdk.init(
        dsn=settings.sentry.dsn,
        # Add data like request headers and IP for users,
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        environment=settings.sentry.environment,
        send_default_pii=settings.sentry.send_default_pii,
        traces_sample_rate=settings.sentry.traces_sample_rate,
        integrations=integrations,
    )


def setup_tracing(name: str | None = __package__):
    """
    Setup OpenTelemetry tracing.

    Tracing is enabled by default, but can be disabled by setting the `KLIKKIKURI_TRACING_ENABLED` setting to `False`.
    """

    from .settings import settings

    if not settings.TRACING_ENABLED:
        logger.debug("Tracing is disabled")
        return None

    name = name or "meri"
    pkg_metadata = metadata(name)

    # Collect resources
    resource = Resource.create({
        SERVICE_NAME: name,
        SERVICE_VERSION: pkg_metadata["version"]
    })
    resources = []
    for detector_pkg, cls in EXTRA_RESOURCE_DETECTOR:
        try:
            logging.debug("Loading extra resource detector %s", detector_pkg)
            mod = import_module(detector_pkg)
            detector_cls = getattr(mod, cls)
            resources.append(detector_cls().detect())
        except ImportError as e:
            logger.debug("Detector %s.%s not found: %s", detector_pkg, cls, e)
            pass
    resource = get_aggregated_resources(resources, resource)

    trace_provider = TracerProvider(resource=resource)

    if settings.TRACING_ENABLED and settings.sentry.dsn and settings.sentry.otel_integration:
        try:
            from sentry_sdk.integrations.opentelemetry import SentrySpanProcessor
            trace_provider.add_span_processor(SentrySpanProcessor())
            logger.debug("SentrySpanProcessor added to TracerProvider")
        except ImportError:
            logger.debug("SentrySpanProcessor not available, skipping Sentry OTel integration")

    # Setup exporter to send traces to otel endpoint
    # TODO: Move to config file
    if otel_endpoint := os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        logger.debug("Setting tracing target to %s", otel_endpoint)
        exporter = OTLPSpanExporter(endpoint=otel_endpoint)
        span_processor = BatchSpanProcessor(exporter)
        trace_provider.add_span_processor(span_processor)

    # TODO: Metrics exporter

    trace.set_tracer_provider(trace_provider)
    tracer = trace.get_tracer(name, pkg_metadata["version"], tracer_provider=trace_provider)

    with tracer.start_as_current_span(f"{name}.tracing.auto_instrumentation") as span:
        if isinstance(span, trace.NonRecordingSpan):
            return None

        for instrumentor_pkg, cls in EXTRA_INSTRUMENTOR:
            try:
                mod = import_module(instrumentor_pkg)
                instrumentor_cls = getattr(mod, cls)
                instrumentor_cls().instrument()
            except ImportError as e:
                logger.info("Instrumentor %s.%s not found: %s", instrumentor_pkg, cls, e)
                pass

    # Use tracer with haystack
    try:
        import haystack.tracing
        haystack.tracing.enable_tracing(haystack.tracing.OpenTelemetryTracer(tracer))
        if settings.DEBUG:
            haystack.tracing.tracer.is_content_tracing_enabled = True
    except ImportError:
        pass

    return tracer

