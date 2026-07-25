"""
Tracing provider configuration and management for OpenTelemetry.
"""

import os
from importlib import import_module
from importlib.metadata import metadata
import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
    get_aggregated_resources,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from niitti.settings.tracing import TracingSettings
from niitti.tracing.crash_buffer import setup_crash_span_dumper
from niitti.tracing.instrumentation import EXTRA_INSTRUMENTOR, EXTRA_RESOURCE_DETECTOR

logger = structlog.get_logger(__name__)

_active_tracer_provider: TracerProvider | None = None
_active_tracer: trace.Tracer | None = None
_instrumented_packages: set[str] = set()


def configure_tracing(
    settings: TracingSettings | None = None,
) -> tuple[TracerProvider, trace.Tracer] | tuple[None, None]:
    """
    Configure and instantiate OpenTelemetry TracerProvider and Tracer without global activation.

    :param settings: TracingSettings/TelemetrySettings instance. If None, default TracingSettings() will be initialized.
    :return: Tuple of (TracerProvider, Tracer) or (None, None) if tracing is disabled.
    """
    if settings is None:
        settings = TracingSettings()

    if not settings.enabled:
        logger.debug("Tracing is disabled in configuration")
        return None, None

    if not settings.service_name:
        raise ValueError(
            "service_name is required to configure tracing. "
            "Please set service_name in telemetry settings or via environment variable OTEL_SERVICE_NAME."
        )

    name = settings.service_name
    try:
        pkg_metadata = metadata(name)
        version = pkg_metadata.get("version", "0.1.0")
    except Exception:
        version = "0.1.0"

    resource = Resource.create(
        {
            SERVICE_NAME: name,
            SERVICE_VERSION: version,
        }
    )

    resources = []
    for detector_pkg, cls in EXTRA_RESOURCE_DETECTOR:
        try:
            logger.debug("Loading extra resource detector %s", detector_pkg)
            mod = import_module(detector_pkg)
            detector_cls = getattr(mod, cls)
            resources.append(detector_cls().detect())
        except ImportError as e:
            logger.debug("Detector %s.%s not found: %s", detector_pkg, cls, e)

    resource = get_aggregated_resources(resources, resource)
    trace_provider = TracerProvider(resource=resource)

    otel_endpoint = settings.endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otel_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore # pyright: ignore[reportMissingImports,reportMissingModuleSource]
                OTLPSpanExporter,
            )

            logger.debug("Setting tracing target to %s", otel_endpoint)
            exporter = OTLPSpanExporter(endpoint=otel_endpoint)
            span_processor = BatchSpanProcessor(exporter)
            trace_provider.add_span_processor(span_processor)
        except ImportError:
            logger.debug("OTLPSpanExporter not available, skipping OTLP export")

    tracer = trace.get_tracer(name, version, tracer_provider=trace_provider)
    return trace_provider, tracer


def activate_tracing(
    trace_provider: TracerProvider,
    tracer: trace.Tracer | None = None,
) -> trace.Tracer:
    """
    Activate an OpenTelemetry TracerProvider globally and register auto-instrumentation handlers.

    :param trace_provider: TracerProvider instance to register globally.
    :param tracer: Tracer instance. If None, obtained from trace_provider.
    :return: OpenTelemetry Tracer instance.
    """
    global _active_tracer_provider, _active_tracer, _instrumented_packages

    _active_tracer_provider = trace_provider

    # Install crash span dumper with SimpleSpanProcessor & bounded RingBufferSpanExporter
    setup_crash_span_dumper(trace_provider)

    trace.set_tracer_provider(trace_provider)
    if tracer is None:
        tracer = trace.get_tracer("niitti", "0.1.0", tracer_provider=trace_provider)
    _active_tracer = tracer

    tracer_name = getattr(tracer, "name", None) or "niitti"
    with tracer.start_as_current_span(f"{tracer_name}.tracing.auto_instrumentation") as span:
        if not isinstance(span, trace.NonRecordingSpan):
            for instrumentor_pkg, cls in EXTRA_INSTRUMENTOR:
                if instrumentor_pkg in _instrumented_packages:
                    continue

                try:
                    mod = import_module(instrumentor_pkg)
                    instrumentor_cls = getattr(mod, cls)
                    instrumentor = instrumentor_cls()
                    if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
                        instrumentor.instrument()
                    _instrumented_packages.add(instrumentor_pkg)
                except ImportError as e:
                    logger.info("Instrumentor %s.%s not found: %s", instrumentor_pkg, cls, e)
                except Exception as e:
                    logger.debug("Error initializing instrumentor %s.%s: %s", instrumentor_pkg, cls, e)

    try:
        import haystack.tracing  # type: ignore # pyright: ignore[reportPrivateImportUsage]

        if hasattr(haystack.tracing, "enable_tracing") and hasattr(haystack.tracing, "OpenTelemetryTracer"):
            haystack.tracing.enable_tracing(haystack.tracing.OpenTelemetryTracer(tracer))  # type: ignore # pyright: ignore[reportPrivateImportUsage]
    except Exception as e:
        logger.debug("Haystack tracing initialization skipped: %s", e)

    # Restore logging configuration in case third-party imports modified structlog or handlers
    try:
        from niitti.logging.config import setup_logging

        setup_logging()
    except Exception as e:
        logger.debug("Logging restoration after tracing setup skipped: %s", e)

    return tracer


def setup_tracing(settings: TracingSettings | None = None) -> trace.Tracer | None:
    """
    Setup OpenTelemetry tracing idempotently.

    Convenience wrapper that configures and activates OpenTelemetry tracing.

    :param settings: TracingSettings instance. If None, default TracingSettings() will be initialized.
    :return: OpenTelemetry Tracer instance or None if tracing is disabled.
    """
    global _active_tracer_provider, _active_tracer

    if settings is None:
        settings = TracingSettings()

    if not settings.enabled:
        logger.debug("Tracing is disabled")
        return None

    if _active_tracer is not None and _active_tracer_provider is not None:
        logger.debug("Tracing is already initialized")
        return _active_tracer

    trace_provider, tracer = configure_tracing(settings)
    if trace_provider is None or tracer is None:
        return None

    return activate_tracing(trace_provider, tracer)


def flush_tracing(timeout_millis: int = 30000) -> bool:
    """
    Force flush active tracing span processors (e.g., BatchSpanProcessor / OTLPSpanExporter).

    :param timeout_millis: Maximum time in milliseconds to wait for export to complete.
    :return: True if flush succeeded, False otherwise.
    """
    global _active_tracer_provider
    if _active_tracer_provider is not None:
        try:
            return bool(_active_tracer_provider.force_flush(timeout_millis=timeout_millis))
        except Exception as e:
            logger.debug("Error flushing trace provider: %s", e)
            return False

    provider = trace.get_tracer_provider()
    force_flush_fn = getattr(provider, "force_flush", None)
    if force_flush_fn is not None:
        try:
            return bool(force_flush_fn(timeout_millis=timeout_millis))
        except Exception as e:
            logger.debug("Error flushing global tracer provider: %s", e)
            return False

    return True


def shutdown_tracing(timeout_millis: int = 30000) -> None:
    """
    Shutdown active tracing span processors and clean up global tracer provider resources.

    :param timeout_millis: Maximum time in milliseconds to wait for shutdown to complete.
    :return: None
    """
    global _active_tracer_provider, _active_tracer
    if _active_tracer_provider is not None:
        try:
            if hasattr(_active_tracer_provider, "shutdown"):
                _active_tracer_provider.shutdown()
        except Exception as e:
            logger.debug("Error shutting down trace provider: %s", e)
        finally:
            _active_tracer_provider = None
            _active_tracer = None
        return

    provider = trace.get_tracer_provider()
    shutdown_fn = getattr(provider, "shutdown", None)
    if shutdown_fn is not None:
        try:
            shutdown_fn(timeout_millis=timeout_millis)
        except Exception as e:
            logger.debug("Error shutting down global tracer provider: %s", e)
    _active_tracer = None
