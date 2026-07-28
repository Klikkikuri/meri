"""
Tests for niitti tracing subpackage.

:purpose: Verify OpenTelemetry tracing setup, crash span dumper, emoji mapping, Sentry integration, and flush/shutdown support.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import niitti.tracing.crash_buffer as crash_buffer_module
import niitti.tracing.provider as provider_module
from niitti.settings.telemetry import TelemetrySettings
from niitti.tracing import (
    DEFAULT_SPAN_EMOJI,
    activate_tracing,
    clear_crash_span_buffer,
    configure_tracing,
    flush_tracing,
    get_span_emoji,
    setup_crash_span_dumper,
    setup_sentry,
    setup_tracing,
    shutdown_tracing,
    span_id_to_emoji,
)


def test_get_span_emoji_mapping():
    """
    Verify get_span_emoji matches exact '.' segments and leaf tokens right-to-left against SPAN_EMOJI_MAP.

    :return: None
    """
    assert get_span_emoji("meri.cli.run") == "🏁"  # right-to-left segment check: run -> cli -> meri
    assert get_span_emoji("pipeline.run") == "🏁"
    assert get_span_emoji("fetch.article") == "📰"
    assert get_span_emoji("fetch_source") == "📡"
    assert get_span_emoji("prune_article") == "📰"
    assert get_span_emoji("generate_title") == "✨"
    assert get_span_emoji("meri.cli.run.fetch_source") == "📡"
    assert get_span_emoji("meri") == "🌊"
    assert get_span_emoji("db") == "🗄️"
    assert get_span_emoji("unknown_action_xyz") == DEFAULT_SPAN_EMOJI
    # Substring / unmapped segment boundary tests
    assert get_span_emoji("domain_name") == DEFAULT_SPAN_EMOJI
    assert get_span_emoji("dashboard") == DEFAULT_SPAN_EMOJI
    assert get_span_emoji("failsafe") == DEFAULT_SPAN_EMOJI






def test_span_id_to_emoji_backwards_compatibility():
    """
    Verify span_id_to_emoji returns DEFAULT_SPAN_EMOJI.

    :return: None
    """
    assert span_id_to_emoji(12345) == DEFAULT_SPAN_EMOJI
    assert span_id_to_emoji("abc") == DEFAULT_SPAN_EMOJI
    assert span_id_to_emoji(None) == DEFAULT_SPAN_EMOJI


def test_clear_crash_span_buffer():
    """
    Verify clear_crash_span_buffer clears in-memory crash exporter if present.

    :return: None
    """
    mock_exporter = MagicMock(spec=InMemorySpanExporter)
    crash_buffer_module._crash_span_exporter = mock_exporter

    clear_crash_span_buffer()
    mock_exporter.clear.assert_called_once()

    # Reset
    crash_buffer_module._crash_span_exporter = None


def test_setup_crash_span_dumper_and_excepthook():
    """
    Verify setup_crash_span_dumper configures excepthook and handles crashes cleanly.

    :return: None
    """
    original_excepthook = sys.excepthook
    provider = TracerProvider()

    setup_crash_span_dumper(provider)
    exporter = setup_crash_span_dumper(trace_provider=None)
    assert exporter is not None
    assert sys.excepthook != original_excepthook

    # Test KeyboardInterrupt pass-through
    mock_old_excepthook = MagicMock()
    with patch("sys.excepthook", mock_old_excepthook):
        try:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt("interrupted"), None)
        finally:
            sys.excepthook = original_excepthook

    # Test Exception handling output
    try:
        current_hook = sys.excepthook
        with patch("sys.stderr.write") as mock_stderr:
            current_hook(RuntimeError, RuntimeError("test crash"), None)
            assert mock_stderr.called
    finally:
        sys.excepthook = original_excepthook
        crash_buffer_module._crash_span_exporter = None


def test_setup_sentry_disabled():
    """
    Verify setup_sentry does nothing when DSN is empty or None.

    :return: None
    """
    with patch("sentry_sdk.init") as mock_init:
        setup_sentry(dsn=None)
        mock_init.assert_not_called()


def test_setup_sentry_enabled():
    """
    Verify setup_sentry initializes sentry_sdk when DSN is provided.

    :return: None
    """
    with patch("sentry_sdk.init") as mock_init:
        setup_sentry(
            dsn="https://key@sentry.io/123",
            environment="production",
            send_logs=False,
            openai_integration=True,
        )
        mock_init.assert_called_once()
        _, kwargs = mock_init.call_args
        assert kwargs["dsn"] == "https://key@sentry.io/123"
        assert kwargs["environment"] == "production"


def test_setup_tracing_disabled():
    """
    Verify setup_tracing returns None when enabled is False.

    :return: None
    """
    settings = TelemetrySettings(enabled=False)
    result = setup_tracing(settings)
    assert result is None


def test_setup_tracing_enabled_and_idempotent():
    """
    Verify setup_tracing initializes OpenTelemetry tracer idempotently.

    :return: None
    """
    provider_module._active_tracer = None
    provider_module._active_tracer_provider = None

    settings = TelemetrySettings(enabled=True, service_name="test_app")
    tracer1 = setup_tracing(settings)
    assert tracer1 is not None

    # Second invocation returns the exact same tracer without duplicating setup
    tracer2 = setup_tracing(settings)
    assert tracer2 is tracer1


def test_configure_tracing_disabled():
    """
    Verify configure_tracing returns (None, None) when enabled is False.

    :return: None
    """
    settings = TelemetrySettings(enabled=False)
    provider, tracer = configure_tracing(settings)
    assert provider is None
    assert tracer is None


def test_configure_and_activate_tracing_separately():
    """
    Verify configure_tracing instantiates providers without activating, and activate_tracing activates them.

    :return: None
    """
    provider_module._active_tracer = None
    provider_module._active_tracer_provider = None

    settings = TelemetrySettings(enabled=True, service_name="test_sep_app")
    provider, tracer = configure_tracing(settings)
    assert provider is not None
    assert tracer is not None
    # Global active tracer state is not updated yet
    assert provider_module._active_tracer_provider is None
    assert provider_module._active_tracer is None

    # Now activate
    activated_tracer = activate_tracing(provider, tracer)
    assert activated_tracer is tracer
    assert provider_module._active_tracer_provider is provider
    assert provider_module._active_tracer is tracer


def test_flush_and_shutdown_tracing():
    """
    Verify flush_tracing and shutdown_tracing execute cleanly on active tracer provider.

    :return: None
    """
    provider_module._active_tracer = None
    provider_module._active_tracer_provider = None

    settings = TelemetrySettings(enabled=True, service_name="test_flush_app")
    tracer = setup_tracing(settings)
    assert tracer is not None

    # Verify flush returns True
    flush_result = flush_tracing(timeout_millis=1000)
    assert flush_result is True

    # Verify shutdown cleans up active tracer state
    shutdown_tracing(timeout_millis=1000)
    assert provider_module._active_tracer_provider is None
    assert provider_module._active_tracer is None


def test_tracing_missing_service_name_raises_value_error():
    """
    Verify configure_tracing and setup_tracing raise ValueError when service_name is missing.

    :return: None
    """
    settings = TelemetrySettings(enabled=True, service_name=None)
    with pytest.raises(ValueError, match="service_name is required"):
        configure_tracing(settings)

    with pytest.raises(ValueError, match="service_name is required"):
        setup_tracing(settings)
