#!/usr/bin/env python3
"""
Verification Script for Niitti Logging & OpenTelemetry Tracing.
===============================================================

This script demonstrates structured logging integrated with OpenTelemetry spans,
context baggage propagation, and the visual crash span waterfall dumper.

Unlike a single end-to-end pipeline run, this version runs several independent
top-level pipelines (each its own trace) before crashing, so the crash span
buffer holds spans from more than one trace at once. That's what makes the
crash dumper render an actual *forest* (one tree per trace) instead of a
single tree.
"""
import argparse
import time

import structlog
from opentelemetry import baggage, trace

from niitti import (
    LoggingSettings,
    TracingSettings,
    setup_logging,
    setup_tracing,
)

logger = structlog.get_logger(__name__)


def process_subtask(item_id: int, should_fail: bool = False) -> None:
    """
    Simulate subtask execution inside a child OpenTelemetry span.
    """
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span(f"subtask_{item_id}") as span:
        span.set_attribute("item_id", item_id)
        logger.info("Processing subtask item", item_id=item_id, status="in_progress")
        time.sleep(0.05)

        if should_fail:
            span.set_attribute("error", True)
            logger.error("Subtask failed unexpectedly", item_id=item_id)
            raise RuntimeError(f"Simulated failure in subtask {item_id}")

        logger.info("Completed subtask item", item_id=item_id, status="done")


def run_pipeline(pipeline_name: str, should_crash: bool = False) -> None:
    """
    Simulate a top-level processing pipeline with nested spans and baggage.

    Each call is its OWN trace (a fresh root span with no parent context),
    so running this multiple times populates the crash buffer with multiple
    independent trees - i.e. a forest.
    """
    tracer = trace.get_tracer(__name__)

    # Set OpenTelemetry baggage for context propagation
    baggage.set_baggage("tenant_id", "klikkikuri-org")
    baggage.set_baggage("environment", "development")

    with tracer.start_as_current_span("run_pipeline") as root_span:
        root_span.set_attribute("pipeline.name", pipeline_name)
        logger.info("Starting article processing pipeline", pipeline_name=pipeline_name)

        with tracer.start_as_current_span("fetch_articles"):
            logger.info("Fetching articles from news sources", source_count=3)
            time.sleep(0.05)

        process_subtask(1)
        process_subtask(2)

        if should_crash:
            process_subtask(3, should_fail=True)

        process_subtask(3)
        logger.info("Pipeline completed successfully", pipeline_name=pipeline_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Niitti Logging and OpenTelemetry Tracing")
    parser.add_argument("--crash", action="store_true", help="Simulate a crash to trigger Span Waterfall Dumper")
    parser.add_argument("--json", action="store_true", help="Force JSON log output format")
    parser.add_argument(
        "--pipelines",
        type=int,
        default=3,
        help="Number of independent (separate-trace) pipelines to run before the crash, "
        "to populate the crash buffer with a forest instead of a single tree",
    )
    args = parser.parse_args()

    # 1. Setup Logging & Tracing
    log_format = "json" if args.json else None
    log_settings = LoggingSettings(LOG_LEVEL="INFO", LOG_FORMAT=log_format or "console")
    trace_settings = TracingSettings(SERVICE_NAME="niitti-verification", TRACING_ENABLED=True)

    setup_logging(log_settings)
    setup_tracing(trace_settings)

    logger.info(
        "Niitti Logging & Tracing Initialized",
        log_format=log_settings.LOG_FORMAT,
        tracing_enabled=trace_settings.TRACING_ENABLED,
    )

    # Run several independent pipelines. Each run_pipeline() call starts a fresh
    # root span with no parent, so each is a distinct trace_id in the crash
    # buffer - this is what produces multiple trees (a forest) in the dumper.
    for i in range(1, args.pipelines + 1):
        is_last = i == args.pipelines
        run_pipeline(f"article_ingestion_{i}", should_crash=(args.crash and is_last))


if __name__ == "__main__":
    main()
