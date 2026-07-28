# Niitti 🪡

**Niitti** is a shared Python package for Klikkikuri services, providing structured logging, OpenTelemetry tracing, Sentry integration, crash span visualization, and Pydantic configuration settings models.

## Features

- 🪵 **Structured Logging**: `structlog` setup with automatic TTY detection (colored console formatting on interactive terminals, JSON logs in non-interactive environments/containers).
- 🔍 **OpenTelemetry Integration**: Automatic trace ID, span ID, baggage injection, and `logger.span()` context managers.
- 🔄 **Flush & Shutdown Support**: `flush_tracing()` to force flush queued batch spans, and `shutdown_tracing()` for clean application teardown.
- 💥 **Crash Span Waterfall Dumper**: Visual tree waterfall of finished OpenTelemetry trace spans rendered to `stderr` on uncaught application crashes using `rich.tree.Tree`.
- 🎯 **Daemon Memory Safety**: `clear_crash_span_buffer()` helper to prevent memory growth in long-running services (e.g., `laituri`).
- ⚙️ **Typed Settings Models**: Modular `LoggingSettings`, `TelemetrySettings`, and `SentrySettings` Pydantic models.

## Installation

Install with specific extras or all features combined:

```bash
uv add "niitti[logging,tracing,settings]"
# Or install all features:
uv add "niitti[all]"
```

Or depend on it in your `pyproject.toml`:

```toml
[dependencies]
niitti = { workspace = true, extras = ["all"] }
```

## Quick Start

### Setup Methods

- **`setup_logging(settings)`**: Configures `structlog` with colored TTY console formatting or JSON container output, and sets `NiittiBoundLogger` as the wrapper class.
- **`setup_tracing(settings)`**: Idempotently initializes the global OpenTelemetry `TracerProvider`, baggage processors, and trace exporter.
- **`setup_sentry(settings)`**: Configures Sentry SDK error reporting.

```python
from niitti import (
    LoggingSettings,
    TelemetrySettings,
    get_logger,
    setup_logging,
    setup_tracing,
    flush_tracing,
    shutdown_tracing,
)
from niitti.sentry import setup_sentry

# 1. Initialize logging, tracing, and Sentry
setup_logging(LoggingSettings(LOG_LEVEL="INFO"))
setup_tracing(TelemetrySettings(service_name="my_service", enabled=True))

logger = get_logger(__name__)

# 2. Force flush or shutdown tracing on application exit
flush_tracing(timeout_millis=5000)
shutdown_tracing()
```

### Logger Spans & Tracing Context

Use `logger.span(...)` with bound loggers, or `from niitti.tracing import span` for decorators:

```python
from niitti import get_logger
from niitti.tracing import span

logger = get_logger(__name__)

# Context manager span yielding OTel span instance
with logger.span("prune_article", url=url) as otel_span:
    otel_span.set_attribute("prune_reason", "unhandled_url")

# Standalone decorator for call sites without a bound logger
@span("extract_article")
def extract_article(url: str):
    ...
```

### Long-Running Daemons (Memory Management)

For long-running background services (e.g. `laituri`), clear the crash span buffer after each top-level iteration tick:

```python
from niitti.tracing import clear_crash_span_buffer

while True:
    run_scheduler_tick()
    clear_crash_span_buffer()  # Prevents in-memory span buffer growth
```

## Optional Dependencies (Extras)

`niitti` provides modular optional dependency extras in `pyproject.toml`:

- `logging`: `structlog`
- `tracing`: `opentelemetry-sdk`, `opentelemetry-instrumentation-logging`, `sentry-sdk`
- `settings`: `pydantic`, `pydantic-settings`
- `all`: Installs all optional dependencies (`niitti[logging,settings,tracing]`)
