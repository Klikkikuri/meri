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

"""
import os
from pathlib import Path
from typing import Literal, Optional, Type
from pydantic import AnyHttpUrl, Field, root_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource
from importlib.metadata import metadata, PackageNotFoundError
from platformdirs import user_config_dir, site_config_dir

_pkg_name: str = __package__
try:
    _pkg_name, *_ = __package__.split(".")
    _pkg_metadata = dict(metadata(_pkg_name))
except (IndexError, PackageNotFoundError):
    _pkg_name = __package__
    _pkg_metadata = dict(metadata(_pkg_name))

_pkg_metadata.setdefault("Home-page", _pkg_metadata.get("repository", ""))


# Locations to look for the settings file
_settings_file_location: list[Path] = [
    Path("/config/config.yaml"),  # Docker settings
    Path.cwd() / "config.yaml",  # Local settings
    Path(site_config_dir("meri")) / "config.yaml",  # System wide settings
    Path(user_config_dir("meri")) / "config.yaml"  # User defined settings
]
if _conf_file := os.getenv("KLIKKIKURI_CONFIG_FILE"):
    _settings_file_location.insert(0, Path(_conf_file))


class GeneratorProviderError(ValueError):
    """
    Error raised when an unknown provider is specified.
    """
    pass

class GeneratorSettings(BaseSettings):
    name: str = Field(..., description="Name of the generator.", required=True)
    provider: Literal["openai", "mistral", "deepseek"] = Field(..., description="Provider of the generator.", required=True)

class OpenAISettings(GeneratorSettings):
    """
    OpenAI settings.

    ..seealso:: https://docs.haystack.deepset.ai/docs/openaigenerator
    """
    provider: str = Field("openai")
    api_key: str = Field(..., description="OpenAI API key.", required=True)
    model: Optional[str]
    api_base_url: Optional[AnyHttpUrl]
    temperature: float = Field(0.)


class MistralSettings(GeneratorSettings):
    provider: str = Field("mistral")
    api_key: str = Field(..., description="Mistral API key.", required=True)
    model: str = Field(..., description="Mistral model.")




class Settings(BaseSettings):
    DEBUG: bool = Field(
        False,
        description="Enable debug mode.",
    )

    TRACING_ENABLED: bool = Field(
        True,
        description="Enable OpenTelemetry tracing.",
    )

    # Crawler settings
    BOT_ID: str = Field(
        "Klikkikuri",
        description="The name of the bot.",
    )
    BOT_HOMEPAGE: str = Field(
        _pkg_metadata["Home-page"],
        description="The homepage of the bot.",
    )
    BOT_USER_AGENT: str = Field(
        r"Mozilla/5.0 (compatible; {BOT_ID}/{Version}; +{Home-page})",
        description="User agent as f-string template for requests. Can be formatted with "
        "package metadata, and `BOT_ID`.",
    )

    # Logging settings
    LOGGING_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        description="Logging level.",
    )

    PROMPT_DIR: Path = Field(Path(user_config_dir("meri"), "prompts"), description="Directory to store prompt templates.")

    llms: list[OpenAISettings] = Field(default_factory=list, description="List of language models to use.")


    @root_validator(pre=True)
    def parse_llm_settings(cls, values):
        llm_list = values.get('llm', [])

        # Map provider literal to class
        provider_to_class = {cls.__fields__['provider'].default: cls for cls in GeneratorSettings.__subclasses__()}

        # Load the settings using the provider class
        settings = []
        for llm in llm_list:
            provider = llm['provider']
            settings_class = provider_to_class.get(provider, None)
            if not settings_class:
                raise GeneratorProviderError(f"Unknown provider: {provider!r}. Available providers: {provider_to_class.keys()}")
            settings.append(settings_class(**llm))

        values['llms'] = settings
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
        env_prefix="KLIKKIKURI_",
    )


settings = Settings()

del _pkg_name, _pkg_metadata, _settings_file_location, _conf_file

if __name__ == "__main__":
    # Test for the settings file
    print(settings.model_dump())
