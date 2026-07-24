import logging
import os
import sys
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
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from niitti.settings.tracing import TracingSettings

logger = structlog.get_logger(__name__)

EXTRA_RESOURCE_DETECTOR = [("opentelemetry.resource.detector.container", "ContainerResourceDetector")]

EXTRA_INSTRUMENTOR = [
    ("opentelemetry.instrumentation.system_metrics", "SystemMetricsInstrumentor"),
    ("opentelemetry.instrumentation.urllib3", "URLLib3Instrumentor"),
    ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ("opentelemetry.instrumentation.jinja2", "Jinja2Instrumentor"),
    ("opentelemetry.instrumentation.openai_v2", "OpenAIInstrumentor"),
    ("opentelemetry.instrumentation.click", "ClickInstrumentor"),
    ("opentelemetry.instrumentation.threading", "ThreadingInstrumentor"),
]

# Deterministic mapping dictionary for known operation types and microservice domains
SPAN_EMOJI_MAP: dict[str, str] = {
    # Pipelines & Core Tasks
    "pipeline": "🚀",
    "run": "🏁",
    "main": "🎬",
    "subtask": "⚡",
    "task": "⚙️",
    # Data & Content Retrieval
    "article": "📰",
    "fetch": "📡",
    "scrape": "🕷️",
    "http": "🌐",
    "request": "📨",
    "download": "📥",
    # AI / LLM / Summarization
    "llm": "🤖",
    "openai": "🧠",
    "prompt": "💬",
    "generate": "✨",
    "summary": "📝",
    # Monorepo Components & Storage
    "meri": "🌊",
    "rahti": "🚢",
    "suola": "🧂",
    "kontio": "🐻",
    "laituri": "⚓",
    "lautta": "🛶",
    "luotsi": "🧭",
    "db": "🗄️",
    "storage": "💾",
    # System & Execution State
    "error": "💥",
    "fail": "❌",
    "crash": "🔥",
    "health": "❤️",
    "setup": "🛠️",
    "init": "🔌",
}

DEFAULT_SPAN_EMOJI: str = "📌"


def get_span_emoji(span_name: str, span_id: int | str | None = None) -> str:
    """
    Deterministically map a span name to a stable emoji using SPAN_EMOJI_MAP.
    Returns DEFAULT_SPAN_EMOJI ("📌") for spans without a mapping.
    """
    name_lower = span_name.lower()
    for key, emoji in SPAN_EMOJI_MAP.items():
        if key in name_lower:
            return emoji

    return DEFAULT_SPAN_EMOJI


def span_id_to_emoji(span_id: int | str | None) -> str:
    """
    Backwards compatibility function for span ID emoji mapping.
    """
    return DEFAULT_SPAN_EMOJI


# In-memory buffer for crash span dumping
_crash_span_exporter: InMemorySpanExporter | None = None


def clear_crash_span_buffer():
    """
    Clear the in-memory crash span buffer.

    Call this function in long-running daemons (e.g. laituri background scheduler)
    after each top-level iteration loop completes to prevent unbounded memory growth.
    """
    global _crash_span_exporter
    if _crash_span_exporter is not None:
        _crash_span_exporter.clear()


def setup_crash_span_dumper(trace_provider: TracerProvider | None = None):
    """
    Attach a SimpleSpanProcessor + InMemorySpanExporter and install a sys.excepthook
    to dump active trace spans to console if the application crashes.
    """
    global _crash_span_exporter
    if _crash_span_exporter is not None and trace_provider is None:
        return _crash_span_exporter

    if trace_provider is None:
        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            trace_provider = provider
        elif isinstance(getattr(provider, "_tracer_provider", None), TracerProvider):
            trace_provider = getattr(provider, "_tracer_provider")

    if _crash_span_exporter is None:
        _crash_span_exporter = InMemorySpanExporter()

    if trace_provider is not None and _crash_span_exporter is not None:
        trace_provider.add_span_processor(SimpleSpanProcessor(_crash_span_exporter))

    old_excepthook = sys.excepthook

    def crash_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            old_excepthook(exc_type, exc_value, exc_tb)
            return

        spans = _crash_span_exporter.get_finished_spans() if _crash_span_exporter else []
        current_span = trace.get_current_span()

        try:
            from rich.console import Console
            from rich.tree import Tree

            console = Console(stderr=True)
            tree = Tree("💥 [bold red]Application Crash - OpenTelemetry Span Waterfall[/bold red]")

            if current_span.is_recording():
                ctx = current_span.get_span_context()
                tree.add(f"[bold yellow]Trace ID:[/bold yellow] {ctx.trace_id:032x}")
                tree.add(f"[bold yellow]Span ID:[/bold yellow]  {ctx.span_id:016x}")

            if spans:
                span_node = tree.add(f"[bold cyan]Recorded Spans ({len(spans)})[/bold cyan]")
                for idx, span in enumerate(spans, 1):
                    duration_ms = (span.end_time - span.start_time) / 1e6 if (span.end_time and span.start_time) else 0
                    status = span.status.status_code.name if span.status else "UNSET"
                    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
                    emoji = get_span_emoji(span.name, span_ctx.span_id if span_ctx else None)
                    node = span_node.add(
                        f"{emoji} [bold white][{idx}] {span.name}[/bold white] (status: [bold]{status}[/bold], duration: {duration_ms:.2f}ms)"
                    )
                    if span.attributes:
                        attr_node = node.add("[dim]Attributes:[/dim]")
                        for k, v in span.attributes.items():
                            attr_node.add(f"[cyan]{k}:[/cyan] {v}")
                    if span.events:
                        event_node = node.add("[dim]Events:[/dim]")
                        for ev in span.events:
                            event_node.add(f"[magenta]{ev.name}:[/magenta] {ev.attributes}")
            else:
                tree.add("[dim]No finished spans recorded in buffer.[/dim]")

            from rich.traceback import Traceback

            console.print()
            console.print(tree)
            console.print()
            console.print(Traceback.from_exception(exc_type, exc_value, exc_tb))
        except ImportError:
            # Plain-text fallback if rich is not installed
            sys.stderr.write("\n" + "=" * 80 + "\n")
            sys.stderr.write("💥 APPLICATION CRASHED - OPENTELEMETRY SPAN TRACE DUMP\n")
            sys.stderr.write("=" * 80 + "\n")
            if current_span.is_recording():
                ctx = current_span.get_span_context()
                sys.stderr.write(f"Trace ID: {ctx.trace_id:032x}\n")
                sys.stderr.write(f"Span ID:  {ctx.span_id:016x}\n")
                sys.stderr.write("-" * 80 + "\n")

            if spans:
                sys.stderr.write(f"Recorded Spans ({len(spans)}):\n")
                for idx, span in enumerate(spans, 1):
                    duration_ms = (span.end_time - span.start_time) / 1e6 if (span.end_time and span.start_time) else 0
                    status = span.status.status_code.name if span.status else "UNSET"
                    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
                    emoji = get_span_emoji(span.name, span_ctx.span_id if span_ctx else None)
                    sys.stderr.write(
                        f" {emoji} [{idx}] Span: {span.name} (status: {status}, duration: {duration_ms:.2f}ms)\n"
                    )
                    if span.attributes:
                        for k, v in span.attributes.items():
                            sys.stderr.write(f"       - {k}: {v}\n")
            sys.stderr.write("=" * 80 + "\n\n")
            old_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = crash_excepthook


def setup_sentry(
    dsn: str | None = None,
    environment: str | None = None,
    send_logs: bool = True,
    send_default_pii: bool = True,
    traces_sample_rate: float = 0.1,
    openai_integration: bool = False,
):
    """
    Setup Sentry SDK.
    """
    if not dsn:
        logger.debug("Sentry DSN not set, skipping Sentry initialization")
        return

    import sentry_sdk

    integrations = []
    if not send_logs:
        try:
            from sentry_sdk.integrations.logging import LoggingIntegration

            integrations.append(LoggingIntegration(event_level=None, level=None))
        except ImportError:
            pass

    if openai_integration:
        try:
            from sentry_sdk.integrations.openai import OpenAIIntegration

            integrations.append(OpenAIIntegration())
        except ImportError:
            logger.debug("OpenAIIntegration not available")

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        send_default_pii=send_default_pii,
        traces_sample_rate=traces_sample_rate,
        integrations=integrations,
        instrumenter="otel",
    )


def setup_tracing(settings: TracingSettings | None = None):
    """
    Setup OpenTelemetry tracing.

    :param settings: TracingSettings instance. If None, default TracingSettings() will be initialized.
    """
    if settings is None:
        settings = TracingSettings()

    if not settings.TRACING_ENABLED:
        logger.debug("Tracing is disabled")
        return None

    name = settings.SERVICE_NAME
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
            logging.debug("Loading extra resource detector %s", detector_pkg)
            mod = import_module(detector_pkg)
            detector_cls = getattr(mod, cls)
            resources.append(detector_cls().detect())
        except ImportError as e:
            logger.debug("Detector %s.%s not found: %s", detector_pkg, cls, e)

    resource = get_aggregated_resources(resources, resource)
    trace_provider = TracerProvider(resource=resource)

    # Install crash span dumper with SimpleSpanProcessor & InMemorySpanExporter
    setup_crash_span_dumper(trace_provider)

    otel_endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otel_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            logger.debug("Setting tracing target to %s", otel_endpoint)
            exporter = OTLPSpanExporter(endpoint=otel_endpoint)
            span_processor = BatchSpanProcessor(exporter)
            trace_provider.add_span_processor(span_processor)
        except ImportError:
            logger.debug("OTLPSpanExporter not available, skipping OTLP export")

    trace.set_tracer_provider(trace_provider)
    tracer = trace.get_tracer(name, version, tracer_provider=trace_provider)

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

    try:
        import haystack.tracing

        if hasattr(haystack.tracing, "enable_tracing") and hasattr(haystack.tracing, "OpenTelemetryTracer"):
            haystack.tracing.enable_tracing(haystack.tracing.OpenTelemetryTracer(tracer))
    except Exception as e:
        logger.debug("Haystack tracing initialization skipped: %s", e)

    # Restore logging configuration in case third-party imports modified structlog or handlers
    try:
        from niitti.logging import setup_logging

        setup_logging()
    except Exception as e:
        logger.debug("Logging restoration after tracing setup skipped: %s", e)

    return tracer
