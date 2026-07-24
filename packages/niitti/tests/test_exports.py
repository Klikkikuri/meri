"""
Tests for top-level package exports of niitti.

:purpose: Verify module attributes and __all__ exports across niitti subpackages.
"""

import niitti
import niitti.settings


def test_top_level_exports():
    """
    Verify top-level niitti package exports.

    :return: None
    """
    expected_exports = [
        "Settings",
        "LoggingSettings",
        "TracingSettings",
        "setup_logging",
        "add_opentelemetry_context",
        "configure_tracing",
        "activate_tracing",
        "setup_tracing",
        "flush_tracing",
        "shutdown_tracing",
        "setup_sentry",
        "setup_crash_span_dumper",
        "clear_crash_span_buffer",
        "SPAN_EMOJI_MAP",
        "DEFAULT_SPAN_EMOJI",
        "get_span_emoji",
        "span_id_to_emoji",
    ]
    for export_name in expected_exports:
        assert hasattr(niitti, export_name), f"Missing export {export_name} in niitti"
        assert export_name in niitti.__all__, f"{export_name} missing from niitti.__all__"


def test_settings_exports():
    """
    Verify niitti.settings package exports.

    :return: None
    """
    expected_exports = [
        "Settings",
        "LoggingSettings",
        "TracingSettings",
        "SentrySettings",
        "DEFAULT_APP_NAME",
        "DEFAULT_APP_AUTHOR",
        "get_package_metadata",
        "lint_yaml_settings_files",
    ]
    for export_name in expected_exports:
        assert hasattr(niitti.settings, export_name), f"Missing export {export_name} in niitti.settings"
        assert export_name in niitti.settings.__all__, f"{export_name} missing from niitti.settings.__all__"
