"""
Tests for niitti settings subpackage.

:purpose: Verify Pydantic settings models, helper functions, and configuration locations.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from niitti.settings.base import BaseNiittiSettings
from niitti.settings.const import DEFAULT_APP_AUTHOR, DEFAULT_APP_NAME
from niitti.settings.logging import LoggingSettings, _default_log_format
from niitti.settings.sentry import SentrySettings
from niitti.settings.settings import Settings, get_package_metadata, lint_yaml_settings_files
from niitti.settings.tracing import TracingSettings


def test_constants():
    """
    Verify application name and author constants.

    :return: None
    """
    assert DEFAULT_APP_NAME == "klikkikuri"
    assert DEFAULT_APP_AUTHOR == "Klikkikuri"


def test_default_log_format_detection():
    """
    Verify _default_log_format behavior based on TTY status.

    :return: None
    """
    with patch("sys.stdout.isatty", return_value=True):
        assert _default_log_format() == "console"

    with patch("sys.stdout.isatty", return_value=False):
        assert _default_log_format() == "json"


def test_logging_settings_defaults_and_method():
    """
    Verify LoggingSettings defaults and get_logging_settings method.

    :return: None
    """
    settings = LoggingSettings(LOG_LEVEL="DEBUG", LOG_FORMAT="json", DEBUG=True)
    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.LOG_FORMAT == "json"
    assert settings.DEBUG is True

    copy_settings = settings.get_logging_settings()
    assert isinstance(copy_settings, LoggingSettings)
    assert copy_settings.LOG_LEVEL == "DEBUG"
    assert copy_settings.LOG_FORMAT == "json"
    assert copy_settings.DEBUG is True


def test_tracing_settings_defaults_and_method():
    """
    Verify TracingSettings defaults and get_tracing_settings method.

    :return: None
    """
    settings = TracingSettings(
        TRACING_ENABLED=False,
        SERVICE_NAME="test_service",
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318",
    )
    assert settings.TRACING_ENABLED is False
    assert settings.SERVICE_NAME == "test_service"
    assert settings.OTEL_EXPORTER_OTLP_ENDPOINT == "http://localhost:4318"

    copy_settings = settings.get_tracing_settings()
    assert isinstance(copy_settings, TracingSettings)
    assert copy_settings.TRACING_ENABLED is False
    assert copy_settings.SERVICE_NAME == "test_service"
    assert copy_settings.OTEL_EXPORTER_OTLP_ENDPOINT == "http://localhost:4318"


def test_sentry_settings_defaults():
    """
    Verify SentrySettings fields and initialization.

    :return: None
    """
    settings = SentrySettings(dsn="https://examplePublicKey@o0.ingest.sentry.io/0", environment="testing")
    assert settings.dsn == "https://examplePublicKey@o0.ingest.sentry.io/0"
    assert settings.environment == "testing"
    assert settings.send_default_pii is True
    assert settings.traces_sample_rate == 0.1
    assert settings.send_logs is True


def test_get_package_metadata():
    """
    Verify get_package_metadata retrieval and caching for known and unknown packages.

    :return: None
    """
    meta_known = get_package_metadata("niitti")
    assert isinstance(meta_known, dict)
    assert "Version" in meta_known
    assert "Home-page" in meta_known

    meta_unknown = get_package_metadata("non_existent_package_xyz_123")
    assert isinstance(meta_unknown, dict)
    assert meta_unknown["Version"] == "0.1.0"
    assert meta_unknown["Home-page"] == "https://github.com/Klikkikuri"

    # Verify mutation of returned dictionary does not mutate cached state
    meta_unknown["Version"] = "999.0.0"
    meta_refetched = get_package_metadata("non_existent_package_xyz_123")
    assert meta_refetched["Version"] == "0.1.0"


def test_lint_yaml_settings_files(tmp_path: Path):
    """
    Verify lint_yaml_settings_files filters invalid or missing YAML files correctly.

    :param tmp_path: pytest fixture providing temporary directory.
    :return: None
    """
    valid_file = tmp_path / "valid.yaml"
    valid_file.write_text("key: value\n", encoding="utf-8")

    non_existent = tmp_path / "missing.yaml"

    invalid_syntax = tmp_path / "invalid_syntax.yaml"
    invalid_syntax.write_text("key: : invalid:\n", encoding="utf-8")

    invalid_type = tmp_path / "invalid_type.yaml"
    invalid_type.write_text("- item1\n- item2\n", encoding="utf-8")

    paths = [valid_file, non_existent, invalid_syntax, invalid_type]
    result = lint_yaml_settings_files(paths)

    assert result == [valid_file]


def test_settings_class_methods():
    """
    Verify BaseSettings inheritance, package name derivation, and config locations.

    :return: None
    """
    assert BaseNiittiSettings is Settings

    class CustomSettings(Settings):
        foo: str = "bar"

    assert Settings.get_package_name() == "niitti" or Settings.get_package_name() == DEFAULT_APP_NAME
    assert CustomSettings.get_package_name() == CustomSettings.__module__.split(".")[0]
    meta = CustomSettings.get_package_metadata()
    assert isinstance(meta, dict)

    locations = CustomSettings.get_default_config_locations()
    assert isinstance(locations, list)


def test_settings_custom_config_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verify KLIKKIKURI_CONFIG_FILE environment variable precedence in config locations.

    :param tmp_path: pytest fixture providing temporary directory.
    :param monkeypatch: pytest monkeypatch fixture.
    :return: None
    """
    custom_cfg = tmp_path / "custom_config.yaml"
    custom_cfg.write_text("custom: true\n", encoding="utf-8")

    monkeypatch.setenv("KLIKKIKURI_CONFIG_FILE", str(custom_cfg))

    locations = Settings.get_default_config_locations()
    assert custom_cfg in locations
    assert locations[0] == custom_cfg


def test_import_niitti_settings_no_import_side_effects():
    """
    Verify importing niitti.settings does not expose module-level load_dotenv.

    :return: None
    """
    import niitti.settings.settings as niitti_settings_module

    assert "load_dotenv()" not in open(niitti_settings_module.__file__, encoding="utf-8").read()


def test_settings_instantiation_loads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verify Settings instantiation invokes load_dotenv using find_dotenv discovery.

    :param tmp_path: pytest fixture providing temporary directory.
    :param monkeypatch: pytest monkeypatch fixture.
    :return: None
    """
    env_file = tmp_path / ".env"
    env_file.write_text("NIITTI_TEST_DOTENV_KEY=custom_value\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NIITTI_TEST_DOTENV_KEY", raising=False)

    class SubSettings(Settings):
        NIITTI_TEST_DOTENV_KEY: str = "default"

    instance = SubSettings()
    assert instance.NIITTI_TEST_DOTENV_KEY == "custom_value"
