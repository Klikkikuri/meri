"""
Unit tests for meri application bootstrap and deferred settings context lifecycle.
"""

import pytest
from niitti.settings.logging import LoggingSettings
from niitti.settings.sentry import SentrySettings
from niitti.settings.telemetry import TelemetrySettings

from meri.bootstrap import setup
from meri.settings import Settings, clear_settings, get_settings, settings


def setup_function():
    """Ensure clean settings state before each test."""
    clear_settings()


def teardown_function():
    """Ensure clean settings state after each test."""
    clear_settings()


def test_out_of_context_settings_raises_runtime_error():
    """Verify accessing settings outside of application context raises RuntimeError."""
    clear_settings()
    assert get_settings() is None

    with pytest.raises(RuntimeError, match="Working outside of application context"):
        _ = settings.llm


def test_setup_context_manager_lifecycle():
    """Verify setup() context manager initializes settings on enter and clears on exit."""
    clear_settings()
    assert get_settings() is None

    with setup() as s:
        assert isinstance(s, Settings)
        assert get_settings() is s
        assert isinstance(s.logging, LoggingSettings)
        assert isinstance(s.sentry, SentrySettings)
        assert isinstance(s.telemetry, TelemetrySettings)
        # Inside context, settings proxy works cleanly
        assert settings.logging is s.logging

    # After exit, settings state is cleared and accessing settings raises RuntimeError
    assert get_settings() is None
    with pytest.raises(RuntimeError, match="Working outside of application context"):
        _ = settings.llm


def test_setup_with_custom_name():
    """Verify setup(name=...) sets telemetry service_name without mutating caller object."""
    custom_settings = Settings()
    original_service_name = custom_settings.telemetry.service_name

    with setup(settings=custom_settings, name="custom-worker-service") as s:
        assert s is custom_settings
        # Original settings object was not mutated in place
        assert custom_settings.telemetry.service_name == original_service_name

    assert get_settings() is None


def test_nested_setup_reentrancy():
    """Verify nested setup() invocations execute safely without crashing."""
    clear_settings()
    with setup(name="outer-app") as outer_settings:
        assert get_settings() is outer_settings
        with setup(name="inner-app"):
            assert get_settings() is outer_settings
        assert get_settings() is outer_settings

    assert get_settings() is None
