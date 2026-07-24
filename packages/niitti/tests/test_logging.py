"""
Tests for niitti logging subpackage.

:purpose: Verify logging setup, level resolution, and OpenTelemetry context injection into structlog.
"""

import logging
from unittest.mock import MagicMock, patch

from niitti.logging import add_opentelemetry_context, get_active_logging_settings, setup_logging
from niitti.settings.logging import LoggingSettings


def test_add_opentelemetry_context_valid_span():
    """
    Verify add_opentelemetry_context injects trace_id and span_id when span is recording and valid.

    :return: None
    """
    mock_span = MagicMock()
    mock_span.is_recording.return_value = True

    mock_ctx = MagicMock()
    mock_ctx.is_valid = True
    mock_ctx.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_ctx.span_id = 0x1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_ctx

    event_dict = {"event": "test_log"}

    with (
        patch("niitti.logging.trace.get_current_span", return_value=mock_span),
        patch("niitti.logging.baggage.get_all", return_value={}),
    ):
        result = add_opentelemetry_context(None, "info", event_dict)

    assert result["trace_id"] == f"{0x1234567890ABCDEF1234567890ABCDEF:032x}"
    assert result["span_id"] == f"{0x1234567890ABCDEF:016x}"
    #assert "otel_baggage" not in result


def test_add_opentelemetry_context_with_baggage():
    """
    Verify add_opentelemetry_context injects baggage into structlog event_dict when present.

    :return: None
    """
    mock_span = MagicMock()
    mock_span.is_recording.return_value = False

    event_dict = {"event": "test_log"}

    with (

        patch("niitti.logging.trace.get_current_span", return_value=mock_span),
        patch("niitti.logging.baggage.get_all", return_value={"request_id": "123"}),
    ):
        result = add_opentelemetry_context(None, "info", event_dict)

    assert "trace_id" not in result
    assert result["otel_baggage"] == {"request_id": "123"}


def test_setup_logging_levels():
    """
    Verify setup_logging sets standard logging levels correctly.

    :return: None
    """
    levels = [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ]

    for level_str, expected_level in levels:
        settings = LoggingSettings(LOG_LEVEL=level_str, LOG_FORMAT="console")  # type: ignore
        setup_logging(settings)

        active = get_active_logging_settings()
        assert active is not None
        assert active.LOG_LEVEL == level_str

        root_logger = logging.getLogger()
        assert root_logger.level == expected_level

    # Test unknown log level fallback in setup_logging match case
    fallback_settings = LoggingSettings(LOG_LEVEL="INFO", LOG_FORMAT="console")
    fallback_settings.LOG_LEVEL = "UNEXPECTED"  # type: ignore
    setup_logging(fallback_settings)
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_debug_override():
    """
    Verify DEBUG=True in LoggingSettings forces root logger level to DEBUG.

    :return: None
    """
    settings = LoggingSettings(LOG_LEVEL="INFO", DEBUG=True)
    setup_logging(settings)

    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG


def test_setup_logging_json_format():
    """
    Verify setup_logging configures JSON log format without raising errors.

    :return: None
    """
    settings = LoggingSettings(LOG_LEVEL="INFO", LOG_FORMAT="json")
    setup_logging(settings)

    root_logger = logging.getLogger()
    assert len(root_logger.handlers) > 0


def test_setup_logging_default_fallback():
    """
    Verify setup_logging works with None parameter.

    :return: None
    """
    setup_logging(None)
    assert get_active_logging_settings() is not None
