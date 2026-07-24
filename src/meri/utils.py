import structlog
from langdetect import detect
from langdetect.detector import Detector
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
    Setup logging for the application. Delegates to niitti.logging.
    """
    from niitti.logging import setup_logging as _niitti_setup_logging
    from niitti.settings.logging import LoggingSettings
    from .settings import settings

    log_level = getattr(settings, "LOG_LEVEL", "INFO")
    debug_flag = debug if debug is not None else getattr(settings, "DEBUG", False)
    log_settings = LoggingSettings(LOG_LEVEL=log_level, DEBUG=debug_flag)
    _niitti_setup_logging(log_settings)


def setup_sentry():
    """
    Setup Sentry SDK. Delegates to niitti.tracing.
    """
    from niitti.tracing import setup_sentry as _niitti_setup_sentry
    from .settings import settings

    _niitti_setup_sentry(
        dsn=settings.sentry.dsn,
        environment=settings.sentry.environment,
        send_logs=settings.sentry.send_logs,
        send_default_pii=settings.sentry.send_default_pii,
        traces_sample_rate=settings.sentry.traces_sample_rate,
        openai_integration=settings.sentry.openai_integration,
    )


def setup_tracing(name: str | None = __package__):
    """
    Setup OpenTelemetry tracing. Delegates to niitti.tracing.
    """
    from niitti.tracing import setup_tracing as _niitti_setup_tracing
    from niitti.settings.tracing import TracingSettings
    from .settings import settings

    name = name or "meri"
    tracing_enabled = getattr(settings, "TRACING_ENABLED", True)
    trace_settings = TracingSettings(SERVICE_NAME=name, TRACING_ENABLED=tracing_enabled)
    return _niitti_setup_tracing(trace_settings)
