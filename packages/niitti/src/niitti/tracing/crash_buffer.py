"""
Crash span ring buffer and waterfall error dumper.
"""

import sys
from collections import defaultdict
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from niitti.tracing.exporters import DEFAULT_CRASH_SPAN_BUFFER_SIZE, RingBufferSpanExporter
from niitti.tracing.instrumentation import get_span_emoji


def _span_id(span: ReadableSpan) -> int | None:
    ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    return ctx.span_id if ctx else None


def _parent_span_id(span: ReadableSpan) -> int | None:
    parent = getattr(span, "parent", None)
    return parent.span_id if parent is not None else None


def _trace_id(span: ReadableSpan) -> int | None:
    ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    return ctx.trace_id if ctx else None


def build_span_forest(
    spans,
) -> list[tuple[int | None, list[tuple[ReadableSpan, list]]]]:
    """
    Reconstruct parent/child span hierarchy from a flat list of finished spans.

    Groups spans by trace_id (a crash buffer may contain spans from more than one
    trace), and within each trace links children to parents via span_id /
    parent_span_id. A span whose parent isn't present in the buffer (e.g. the
    parent finished and was evicted from the ring buffer, or the parent belongs
    to a different process) is treated as a root for display purposes.

    :param spans: Iterable of finished ReadableSpan objects.
    :return: List of (trace_id, roots) tuples, where each root is a
        (span, children) tuple and children is recursively the same
        (span, children) list structure, in the original span order.
    """
    spans_by_trace: dict[int | None, list[ReadableSpan]] = defaultdict(list)
    for span in spans:
        spans_by_trace[_trace_id(span)].append(span)

    forest: list[tuple[int | None, list[tuple[ReadableSpan, list]]]] = []

    for trace_id_value, trace_spans in spans_by_trace.items():
        by_id: dict[int, ReadableSpan] = {}
        for span in trace_spans:
            sid = _span_id(span)
            if sid is not None:
                by_id[sid] = span

        children_of: dict[int, list[ReadableSpan]] = defaultdict(list)
        roots: list[ReadableSpan] = []
        for span in trace_spans:
            pid = _parent_span_id(span)
            if pid is not None and pid in by_id:
                children_of[pid].append(span)
            else:
                roots.append(span)

        def attach(span: ReadableSpan) -> tuple[ReadableSpan, list]:
            sid = _span_id(span)
            child_spans = children_of.get(sid, []) if sid is not None else []
            return (span, [attach(child) for child in child_spans])

        forest.append((trace_id_value, [attach(root) for root in roots]))

    return forest


# In-memory ring buffer for crash span dumping
_crash_span_exporter: RingBufferSpanExporter | None = None


def clear_crash_span_buffer() -> None:
    """
    Clear the crash span ring buffer.

    Kept for backwards compatibility. Since the buffer is now a bounded ring
    buffer (see RingBufferSpanExporter), calling this is no longer required
    to prevent unbounded memory growth in long-running daemons; it remains
    useful if you want to explicitly reset the buffer between iterations
    (e.g. to avoid showing spans from a prior loop iteration on crash).
    """
    global _crash_span_exporter
    if _crash_span_exporter is not None:
        _crash_span_exporter.clear()


def setup_crash_span_dumper(
    trace_provider: TracerProvider | None = None,
    max_spans: int = DEFAULT_CRASH_SPAN_BUFFER_SIZE,
) -> RingBufferSpanExporter | None:
    """
    Attach a SimpleSpanProcessor + bounded RingBufferSpanExporter and install a
    sys.excepthook to dump active trace spans to console if the application crashes.

    :param trace_provider: TracerProvider to attach the crash span processor to.
    :param max_spans: Maximum number of finished spans retained for crash dumping.
        Older spans are evicted automatically once this cap is reached, so this
        buffer is safe to use in long-running daemons without manual clearing.
    :return: Active RingBufferSpanExporter instance.
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
        _crash_span_exporter = RingBufferSpanExporter(maxlen=max_spans)

    if trace_provider is not None and _crash_span_exporter is not None:
        already_registered = False
        if hasattr(trace_provider, "_active_span_processor"):
            active_processor = getattr(trace_provider, "_active_span_processor")
            if hasattr(active_processor, "_span_processors"):
                for proc in active_processor._span_processors:
                    if (
                        isinstance(proc, SimpleSpanProcessor)
                        and getattr(proc, "span_exporter", None) is _crash_span_exporter
                    ):
                        already_registered = True
                        break

        if not already_registered:
            trace_provider.add_span_processor(SimpleSpanProcessor(_crash_span_exporter))

    old_excepthook = sys.excepthook

    def crash_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            old_excepthook(exc_type, exc_value, exc_tb)
            return

        spans = _crash_span_exporter.get_finished_spans() if _crash_span_exporter else ()
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
                buffer_note = f" (ring buffer, max {_crash_span_exporter.maxlen})" if _crash_span_exporter else ""
                span_node = tree.add(f"[bold cyan]Recorded Spans ({len(spans)}){buffer_note}[/bold cyan]")

                def add_rich_node(parent_node, span: ReadableSpan, children: list) -> None:
                    duration_ms = (span.end_time - span.start_time) / 1e6 if (span.end_time and span.start_time) else 0
                    status = span.status.status_code.name if span.status else "UNSET"
                    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
                    emoji = get_span_emoji(span.name, span_ctx.span_id if span_ctx else None)
                    node = parent_node.add(
                        f"{emoji} [bold white]{span.name}[/bold white] (status: [bold]{status}[/bold], duration: {duration_ms:.2f}ms)"
                    )
                    if span.attributes:
                        attr_node = node.add("[dim]Attributes:[/dim]")
                        for k, v in span.attributes.items():
                            attr_node.add(f"[cyan]{k}:[/cyan] {v}")
                    if span.events:
                        event_node = node.add("[dim]Events:[/dim]")
                        for ev in span.events:
                            event_node.add(f"[magenta]{ev.name}:[/magenta] {ev.attributes}")
                    for child_span, child_children in children:
                        add_rich_node(node, child_span, child_children)

                forest = build_span_forest(spans)
                for trace_id_value, roots in forest:
                    trace_label = f"{trace_id_value:032x}" if trace_id_value is not None else "unknown"
                    trace_node = (
                        span_node.add(f"[bold yellow]Trace {trace_label}[/bold yellow]")
                        if len(forest) > 1
                        else span_node
                    )
                    for root_span, root_children in roots:
                        add_rich_node(trace_node, root_span, root_children)
            else:
                tree.add("[dim]No finished spans recorded in buffer.[/dim]")

            from rich.traceback import Traceback

            console.print()
            console.print(tree)
            console.print()
            console.print(Traceback.from_exception(exc_type, exc_value, exc_tb))

            old_excepthook(exc_type, exc_value, exc_tb)
        except ImportError:
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

                def write_plain_node(span: ReadableSpan, children: list, depth: int) -> None:
                    indent = "  " * depth
                    branch = "└─ " if depth > 0 else ""
                    duration_ms = (span.end_time - span.start_time) / 1e6 if (span.end_time and span.start_time) else 0
                    status = span.status.status_code.name if span.status else "UNSET"
                    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
                    emoji = get_span_emoji(span.name, span_ctx.span_id if span_ctx else None)
                    sys.stderr.write(
                        f"{indent}{branch}{emoji} {span.name} (status: {status}, duration: {duration_ms:.2f}ms)\n"
                    )
                    if span.attributes:
                        for k, v in span.attributes.items():
                            sys.stderr.write(f"{indent}       - {k}: {v}\n")
                    for child_span, child_children in children:
                        write_plain_node(child_span, child_children, depth + 1)

                forest = build_span_forest(spans)
                for trace_id_value, roots in forest:
                    if len(forest) > 1:
                        trace_label = f"{trace_id_value:032x}" if trace_id_value is not None else "unknown"
                        sys.stderr.write(f"Trace {trace_label}:\n")
                    for root_span, root_children in roots:
                        write_plain_node(root_span, root_children, 0)
            sys.stderr.write("=" * 80 + "\n\n")
            old_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = crash_excepthook
    return _crash_span_exporter
