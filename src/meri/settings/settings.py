"""
Configuration settings.
=======================

This module defines the configuration settings for the Klikkikuri 🦈 service.

Order of precedence:
    1. Environment variables
    2. `.env` file
    3. Secrets directory (e.g. `/run/secrets`).
    4. YAML configuration file, with the following locations:
        - User defined settings file ($KLIKKIKURI_CONFIG_FILE)
        - User defined settings ($XDG_CONFIG_HOME)
        - System wide settings ($XDG_CONFIG_DIRS)
        - Local settings: `./config.yaml`
        - Docker settings: `/config/config.yaml`
        - Devcontainer user settings: `/app/config.yaml`

"""
import logging
import os
from importlib.metadata import PackageNotFoundError, metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Literal, Type

# Ugly duckling hack – load .env before initializing settings, to ensure that environment variables are available
from dotenv import load_dotenv
from platformdirs import site_config_dir, user_config_dir
from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .const import (
    DEFAULT_BOT_ID,
    PKG_NAME,
)
from .llms import (
    GeneratorProviderError,
    GeneratorSettings,
    LLMSetting,
    detect_generators,
)
from .newssources import NewsSource
from .rahti import RahtiSettings
from .sentry import SentrySettings

load_dotenv()

logger = logging.getLogger(__name__)
if os.getenv("DEBUG", "0") == "1":
    logger.setLevel(logging.DEBUG)

_pkg_metadata: dict = {}

try:
    _pkg_name, *_ = PKG_NAME.split(".")
    _pkg_metadata = dict(metadata(_pkg_name))
except (IndexError, PackageNotFoundError):
    _pkg_name = PKG_NAME
    _pkg_metadata = dict(metadata(_pkg_name))
finally:
    # Set the homepage from the metadata
    _pkg_metadata.setdefault("Home-page", _pkg_metadata.get("Project-URL", "").split(", ")[1])

# User defined settings
_user_config_path = Path(user_config_dir(PKG_NAME), "config.yaml")
DEFAULT_CONFIG_PATH = _user_config_path

# Locations to look for the settings file
# notice: order is reversed to give precedence to the user defined settings
_settings_file_location: list[Path] = [
    Path("/app/config.yaml"),  # Devcontainer user settings
    Path("/config/config.yaml"),  # Docker settings
    Path.cwd() / "config.yaml",  # Local settings
    Path(site_config_dir(PKG_NAME)) / "config.yaml",  # System wide settings
    _user_config_path
]
if _conf_file := os.getenv("KLIKKIKURI_CONFIG_FILE"):
    _conf_file = Path(_conf_file)
    _settings_file_location.insert(0, _conf_file)
    DEFAULT_CONFIG_PATH = _conf_file

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None

_otel_available: bool = find_spec("opentelemetry.exporter") is not None

# Default Suola rules path from monorepo
_suola_rules = Path("packages/suola/rules.yaml").resolve()


class Settings(BaseSettings):
    DEBUG: bool = Field(
        False,
        description="Enable debug mode.",
    )

    TRACING_ENABLED: bool = Field(
        _otel_available,
        description="Enable OpenTelemetry tracing.",
    )

    sentry: SentrySettings = Field(
        default_factory=SentrySettings,  # type: ignore
        description="Sentry settings.",
    )

    BOT_ID: str = Field(DEFAULT_BOT_ID, description="Bot ID.")
    BOT_USER_AGENT: str = Field(
        "Mozilla/5.0 (compatible;)",
        description="User agent as f-string template for requests. Can be formatted with "
        "package metadata, and `BOT_ID`.",
    )

    # Logging settings
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        description="Logging level.",
    )

    REQUESTS_CACHE: bool = Field(_requests_cache_available, description="Enable requests cache.")
    MAX_WORKERS: int = Field(3, description="Maximum number of worker threads for processing articles.")

    PROMPT_DIR: Path = Field(Path(user_config_dir(PKG_NAME), "prompts"), description="Directory to store prompt templates.")

    llm: list[LLMSetting] = Field(default_factory=list, description="List of language models to use.")
    pipelines: list[str] = Field([], description="List of pipeline definitions.")

    sources: list[NewsSource] = Field(default_factory=list, description="List of news sources to scrape.")

    suola_rules: Path | None = Field(
        _suola_rules if _suola_rules.exists() else None,
        description="Path to Suola rules file. If not set, inbuilt rules will be used.",
    )

    rahti: RahtiSettings

    @model_validator(mode="before")
    @classmethod
    def parse_llm_settings(cls, values):
        _logger = logging.getLogger(__name__).getChild("parse_llm_settings")
        _logger.debug(f"Values: {values}")
        llm_list = values.get('llm', [])

        # Map provider literal to class
        provider_to_class = {model_cls.model_fields['provider'].default: model_cls for model_cls in GeneratorSettings.__subclasses__()}
        _logger.debug(f"Provider to class: {provider_to_class}")

        # Load the settings using the provider class
        settings = []
        for llm in llm_list:
            provider = llm['provider']
            settings_class = provider_to_class.get(provider, None)
            if not settings_class:
                raise GeneratorProviderError(f"Unknown provider: {provider!r}. Available providers: {provider_to_class.keys()}")
            settings.append(settings_class(**llm))

        if len(settings) == 0:
            settings += detect_generators(values)

        _logger.debug("Validated LLM provider settings with %d provider", len(settings), extra={"settings": settings})
        values['llm'] = settings
        return values


    @model_validator(mode="before")
    @classmethod
    def _compute_user_agent(cls, values):
        """
        Compute the user-agent string.
        """
        bot_info = _pkg_metadata.copy()
        bot_info.setdefault("BOT_ID", values.get("BOT_ID", DEFAULT_BOT_ID))
        user_agent = "Mozilla/5.0 (compatible; {BOT_ID}/{Version}; +{Home-page})".format(**bot_info)
        values.setdefault('BOT_USER_AGENT', user_agent)
        return values

    model_config = SettingsConfigDict(
        secrets_dir='/run/secrets' if Path('/run/secrets').exists() else None,
        yaml_file=_settings_file_location,
        yaml_file_encoding="utf-8",
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',  # If dotenv contains extra keys, ignore them
        env_nested_delimiter='__',
    )

    @classmethod
    def settings_customise_sources(cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            YamlConfigSettingsSource(settings_cls),
        )


settings: Settings = Settings()  # type: ignore

def init_settings(**kwargs) -> Settings:
    """
    Initialize and return the settings.
    """
    global settings
    s = Settings(**kwargs)  # type: ignore
    settings = s
    return s
