"""
Tests for top-level and subpackage exports of niitti.

:purpose: Verify module attributes and __all__ exports across niitti packages and subpackages.
"""

import niitti
import niitti.logging
import niitti.sentry
import niitti.settings
import niitti.tracing


def test_top_level_exports():
    """
    Verify minimal top-level niitti package exports.

    :return: None
    """
    expected_exports = [
        "Settings",
        "SettingsProxy",
        "LoggingSettings",
        "TelemetrySettings",
        "setup_logging",
        "setup_tracing",
        "flush_tracing",
        "shutdown_tracing",
    ]
    for export_name in expected_exports:
        assert hasattr(niitti, export_name), f"Missing export {export_name} in niitti"
        assert export_name in niitti.__all__, f"{export_name} missing from niitti.__all__"

    # Implementation details and lower-level functions should NOT be in root __all__
    lower_level_exports = [
        "add_opentelemetry_context",
        "configure_tracing",
        "activate_tracing",
        "setup_sentry",
        "setup_crash_span_dumper",
        "clear_crash_span_buffer",
        "SPAN_EMOJI_MAP",
        "DEFAULT_SPAN_EMOJI",
        "get_span_emoji",
        "span_id_to_emoji",
    ]
    for export_name in lower_level_exports:
        assert export_name not in niitti.__all__, f"Internal detail {export_name} should not be in niitti.__all__"


def test_logging_exports():
    """
    Verify niitti.logging subpackage exports.

    :return: None
    """
    expected_exports = [
        "setup_logging",
        "add_opentelemetry_context",
        "get_active_logging_settings",
    ]
    for export_name in expected_exports:
        assert hasattr(niitti.logging, export_name), f"Missing export {export_name} in niitti.logging"
        assert export_name in niitti.logging.__all__, f"{export_name} missing from niitti.logging.__all__"


def test_tracing_exports():
    """
    Verify niitti.tracing subpackage exports.

    :return: None
    """
    expected_exports = [
        "setup_tracing",
        "configure_tracing",
        "activate_tracing",
        "flush_tracing",
        "shutdown_tracing",
        "setup_crash_span_dumper",
        "clear_crash_span_buffer",
        "build_span_forest",
        "RingBufferSpanExporter",
        "get_span_emoji",
        "span_id_to_emoji",
        "SPAN_EMOJI_MAP",
        "DEFAULT_SPAN_EMOJI",
    ]
    for export_name in expected_exports:
        assert hasattr(niitti.tracing, export_name), f"Missing export {export_name} in niitti.tracing"
        assert export_name in niitti.tracing.__all__, f"{export_name} missing from niitti.tracing.__all__"


def test_sentry_exports():
    """
    Verify niitti.sentry subpackage exports.

    :return: None
    """
    expected_exports = ["setup_sentry"]
    for export_name in expected_exports:
        assert hasattr(niitti.sentry, export_name), f"Missing export {export_name} in niitti.sentry"
        assert export_name in niitti.sentry.__all__, f"{export_name} missing from niitti.sentry.__all__"


def test_settings_exports():
    """
    Verify niitti.settings subpackage exports.

    :return: None
    """
    expected_exports = [
        "Settings",
        "SettingsProxy",
        "LoggingSettings",
        "TelemetrySettings",
        "SentrySettings",
        "DEFAULT_APP_NAME",
        "DEFAULT_APP_AUTHOR",
        "get_package_metadata",
        "lint_yaml_settings_files",
    ]
    for export_name in expected_exports:
        assert hasattr(niitti.settings, export_name), f"Missing export {export_name} in niitti.settings"
        assert export_name in niitti.settings.__all__, f"{export_name} missing from niitti.settings.__all__"
