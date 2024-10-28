from importlib import import_module
import logging
import os
from opentelemetry import trace
from opentelemetry.sdk.resources import (
    Resource,
    SERVICE_NAME,
    SERVICE_VERSION,
    get_aggregated_resources

)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from .settings import settings
import structlog

from importlib.metadata import metadata

logger = structlog.get_logger(__name__)

EXTRA_RESOURCE_DETECTOR = [
    ("opentelemetry.resource.detector.container", "ContainerResourceDetector")
]
""" List of extra resource detectors to use, if available. """

EXTRA_INSTRUMENTOR = [
    ("opentelemetry.instrumentation.system_metrics", "SystemMetricsInstrumentor"),
    ("opentelemetry.instrumentation.asyncio", "AsyncioInstrumentor"),
    ("opentelemetry.instrumentation.urllib3", "URLLib3Instrumentor"),
    ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ("opentelemetry.instrumentation.jinja2", "Jinja2Instrumentor"),
    ("opentelemetry.instrumentation.openai", "OpenAIInstrumentor"),
    # SQLAlchemyInstrumentor is not included here, it's included in the `get_db` function
]
""" List of extra instrumentors to use, if available. """

def add_open_telemetry_spans(_, __, event_dict):
    span = trace.get_current_span()
    if not span.is_recording():
        event_dict["span"] = None
        return event_dict  

    ctx = span.get_span_context()
    parent = getattr(span, "parent", None)

    event_dict["span"] = {
        "span_id": hex(ctx.span_id),
        "trace_id": hex(ctx.trace_id),
        "parent_span_id": None if not parent else hex(parent.span_id),
    }

    return event_dict


def setup_logging(debug=settings.DEBUG):

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            add_open_telemetry_spans,  # Add OpenTelemetry context to logs
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        pass_foreign_args=True,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
        ],
    )
    handler = logging.StreamHandler()

    # Use OUR `ProcessorFormatter` to format all `logging` entries.
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    log_level = settings.LOGGING_LEVEL
    root_logger.setLevel(log_level)
    logging.getLogger("haystack").setLevel(log_level)

    LoggingInstrumentor().instrument()

    # Set the top-level module to DEBUG if debug is True
    if debug:
        logging.getLogger(__package__).setLevel(logging.DEBUG)
        return


def setup_tracing(name: str = __package__):
    """
    Setup OpenTelemetry tracing.

    Tracing is enabled by default, but can be disabled by setting the `KLIKKIKURI_TRACING_ENABLED` setting to `False`.
    """

    if not settings.TRACING_ENABLED:
        logger.debug("Tracing is disabled")
        return None

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

    # Setup exporter to send traces to otel endpoint
    # TODO: Move to config file
    if otel_endpoint := os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        logger.debug("Setting tracing target to %s", otel_endpoint)
        exporter = OTLPSpanExporter(endpoint=otel_endpoint)
        span_processor = BatchSpanProcessor(exporter)
        trace_provider.add_span_processor(span_processor)

    # TODO: Metrics exporter

    trace.set_tracer_provider(trace_provider)
    tracer = trace.get_tracer(name, pkg_metadata["version"], tracer_provider=trace_provider)

    with tracer.start_as_current_span(f"{name}.tracing.auto_enable") as span:
        if isinstance(span, trace.NonRecordingSpan):
            return None

        for instrumentor_pkg, cls in EXTRA_INSTRUMENTOR:
            try:
                mod = import_module(instrumentor_pkg)
                instrumentor_cls = getattr(mod, cls)
                instance = instrumentor_cls().instrument()
                logger.debug("Instrumentor %s.%s loaded", instrumentor_pkg, cls, extra={"instance": instance})
            except ImportError as e:
                logger.debug("Instrumentor %s.%s not found: %s", instrumentor_pkg, cls, e)
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

