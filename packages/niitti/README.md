# Niitti 🪡

**Niitti** is a shared Python package for Klikkikuri services, providing structured logging, OpenTelemetry tracing, Sentry integration, crash span visualization, and Pydantic configuration settings models.

## Features

- 🪵 **Structured Logging**: `structlog` setup with automatic TTY detection (colored console formatting on interactive terminals, JSON logs in non-interactive environments/containers).
- 🔍 **OpenTelemetry Integration**: Automatic trace ID, span ID, and OpenTelemetry Baggage injection into all log messages.
- 💥 **Crash Span Waterfall Dumper**: Visual tree waterfall of finished OpenTelemetry trace spans rendered to `stderr` on uncaught application crashes using `rich.tree.Tree` (with plain-text fallback).
- 🎯 **Daemon Memory Safety**: `clear_crash_span_buffer()` helper to prevent memory growth in long-running services (e.g., `laituri`).
- ⚙️ **Typed Settings Models**: Modular `LoggingSettings` and `TracingSettings` Pydantic models.

## Installation

Within the Klikkikuri workspace:

```bash
uv add niitti
```

Or depend on it in your `pyproject.toml`:

```toml
[dependencies]
niitti = { workspace = true }
```

## Quick Start

### Logging & Tracing Setup

```python
from niitti import setup_logging, setup_tracing, LoggingSettings, TracingSettings

# 1. Initialize logging
log_settings = LoggingSettings(LOG_LEVEL="INFO")
setup_logging(log_settings)

# 2. Initialize tracing
trace_settings = TracingSettings(SERVICE_NAME="meri", TRACING_ENABLED=True)
tracer = setup_tracing(trace_settings)
```

### Structlog with OTel Context

```python
import structlog
from opentelemetry import baggage

logger = structlog.get_logger(__name__)

# Set OpenTelemetry baggage for context propagation
baggage.set_baggage("tenant_id", "acme-corp")

# Log event automatically includes trace_id, span_id, and otel_baggage
logger.info("Processing article", article_id=42)
```

### Long-Running Daemons (Memory Management)

For long-running background services (e.g. `laituri`), clear the crash span buffer after each top-level iteration tick:

```python
from niitti import clear_crash_span_buffer

while True:
    run_scheduler_tick()
    clear_crash_span_buffer()  # Prevents in-memory span buffer growth
```

## Dependency Groups

`niitti` provides modular dependency groups in `pyproject.toml`:

- `logging`: `structlog`
- `tracing`: `opentelemetry-sdk`, `opentelemetry-instrumentation-logging`, `sentry-sdk`
- `settings`: `pydantic`, `pydantic-settings`
