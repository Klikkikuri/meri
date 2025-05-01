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
from importlib.util import find_spec
import logging
import os
from contextvars import ContextVar
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Literal, Type

from platformdirs import site_config_dir, user_config_dir
from pydantic import Field, root_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .llms import (
    GeneratorProviderError,
    GeneratorSettings,
    OllamaSettings,
    GoogleGeminiSettings,
    OpenAISettings,
    detect_generators,
)

LLMSetting = OpenAISettings | OllamaSettings | GoogleGeminiSettings | GeneratorSettings

logger = logging.getLogger(__name__)

DEFAULT_BOT_ID = "Klikkikuri"

_pkg_name: str = __package__
try:
    _pkg_name, *_ = __package__.split(".")
    _pkg_metadata = dict(metadata(_pkg_name))
except (IndexError, PackageNotFoundError):
    _pkg_name = __package__
    _pkg_metadata = dict(metadata(_pkg_name))
finally:
    # Set the homepage from the metadata
    _pkg_metadata.setdefault("Home-page", _pkg_metadata.get("Project-URL", "").split(", ")[1])


# User defined settings
_user_config_path = Path(user_config_dir("meri"), "config.yaml")
DEFAULT_CONFIG_PATH = _user_config_path

# Locations to look for the settings file
# notice: order is reversed to give precedence to the user defined settings
_settings_file_location: list[Path] = [
    Path("/app/config.yaml"),  # Devcontainer user settings
    Path("/config/config.yaml"),  # Docker settings
    Path.cwd() / "config.yaml",  # Local settings
    Path(site_config_dir("meri")) / "config.yaml",  # System wide settings
    _user_config_path
]
if _conf_file := os.getenv("KLIKKIKURI_CONFIG_FILE"):
    _conf_file = Path(_conf_file)
    _settings_file_location.insert(0, _conf_file)
    DEFAULT_CONFIG_PATH = _conf_file

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None


class Settings(BaseSettings):
    DEBUG: bool = Field(
        False,
        description="Enable debug mode.",
    )

    TRACING_ENABLED: bool = Field(
        True,
        description="Enable OpenTelemetry tracing.",
    )


    BOT_ID: str = Field(DEFAULT_BOT_ID, description="Bot ID.")
    BOT_USER_AGENT: str = Field(
        "Mozilla/5.0 (compatible;)",
        description="User agent as f-string template for requests. Can be formatted with "
        "package metadata, and `BOT_ID`.",
    )

    # Logging settings
    LOGGING_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        description="Logging level.",
    )

    REQUESTS_CACHE: bool = Field(_requests_cache_available, description="Enable requests cache.")

    PROMPT_DIR: Path = Field(Path(user_config_dir("meri"), "prompts"), description="Directory to store prompt templates.")

    llm: list[LLMSetting] = Field(default_factory=list, description="List of language models to use.")
    pipelines: list[str] = Field([], description="List of pipeline definitions.")

    @root_validator(pre=True)
    def parse_llm_settings(cls, values):
        _logger = logging.getLogger(__name__).getChild("parse_llm_settings")
        _logger.debug(f"Values: {values}")
        llm_list = values.get('llm', [])

        # Map provider literal to class
        provider_to_class = {cls.__fields__['provider'].default: cls for cls in GeneratorSettings.__subclasses__()}
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


    @root_validator(pre=True)
    def _compute_user_agent(cls, values):
        """
        Compute the user-agent string.
        """
        bot_info = _pkg_metadata.copy()
        bot_info.setdefault("BOT_ID", values.get("BOT_ID", DEFAULT_BOT_ID))
        user_agent = "Mozilla/5.0 (compatible; {BOT_ID}/{Version}; +{Home-page})".format(**bot_info)
        values.setdefault('BOT_USER_AGENT', user_agent)
        return values


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

    model_config = SettingsConfigDict(
        secrets_dir='/run/secrets',
        yaml_file=_settings_file_location,
        yaml_file_encoding="utf-8",
        env_prefix="",
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',  # If dotenv contains extra keys, ignore them
    )


settings_var: ContextVar[Settings] = ContextVar(f"{__package__}.settings_var", default=Settings())
settings = settings_var.get()
