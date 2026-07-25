"""
Configuration settings.
=======================

This module defines the configuration settings for the Klikkikuri 🦈 service.

Order of precedence:
    1. Environment variables
    2. `.env` file
    3. Secrets directory (e.g. `/run/secrets`).
    4. YAML configuration file, with standard locations:
        - Devcontainer user settings: `/app/config.yaml`
        - Instance folder settings: `/app/instance/config.yaml`
        - Docker settings: `/config/config.yaml`
        - Local settings: `./config.yaml`
        - System wide settings ($XDG_CONFIG_DIRS / site_config_dir)
        - User defined settings ($XDG_CONFIG_HOME / user_config_dir)

"""
import logging
import os
from importlib.util import find_spec
from pathlib import Path

# Ugly duckling hack – load .env before initializing settings, to ensure that environment variables are available
from dotenv import load_dotenv
from niitti.settings.logging import LoggingSettings
from niitti.settings.sentry import SentrySettings
from niitti.settings.settings import Settings as NiittiSettings
from niitti.settings.telemetry import TelemetrySettings
from platformdirs import user_config_dir
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

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

load_dotenv()

logger = logging.getLogger(__name__)
if os.getenv("DEBUG", "0") == "1":
    logger.setLevel(logging.DEBUG)

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None

_otel_available: bool = find_spec("opentelemetry.exporter") is not None

# Default Suola rules path from monorepo
_suola_rules = Path("packages/suola/rules.yaml").resolve()


def _iter_subclasses(base_cls):
    """
    Helper function to iterate over all subclasses of a base class, including indirect subclasses.
    """
    for sub_cls in base_cls.__subclasses__():
        yield sub_cls
        yield from _iter_subclasses(sub_cls)


class SkipProcessingSettings(BaseModel):
    """
    Settings for skipping article title processing.
    """

    labels: list[str] = Field(
        default_factory=lambda: ["paywalled=true"],
        description="List of Kubernetes-style label selectors (e.g. 'paywalled=true', 'article-type = opinion') that skip title generation and are stored in Rahti without processing.",
    )

    @field_validator("labels")
    @classmethod
    def validate_label_selectors(cls, v: list[str]) -> list[str]:
        from meri.labels import LabelSelector

        for selector_str in v:
            LabelSelector.parse(selector_str)
        return v


class Settings(NiittiSettings, LoggingSettings):  # pyright: ignore[reportIncompatibleVariableOverride]
    sentry: SentrySettings = Field(
        default_factory=SentrySettings,  # type: ignore
        description="Sentry settings.",
    )
    telemetry: TelemetrySettings = Field(
        default_factory=TelemetrySettings,
        description="Telemetry settings.",
    )
    BOT_ID: str = Field(DEFAULT_BOT_ID, description="Bot ID.")
    BOT_USER_AGENT: str = Field(
        "Mozilla/5.0 (compatible;)",
        description="User agent as f-string template for requests. Can be formatted with "
        "package metadata, and `BOT_ID`.",
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

    url_blacklist: list[str] = Field(default_factory=list, description="List of URL patterns to ignore.")
    """
    URL patterns to ignore. Can be substrings or regex patterns (if enclosed in slashes, e.g. `/pattern/`).

    This is to limit the scope of scraping to relevant sites, and not to log suola errors for irrelevant sites.
    """

    skip_processing: SkipProcessingSettings = Field(
        default_factory=SkipProcessingSettings,
        description="Settings for skipping article title processing.",
    )

    rahti: RahtiSettings

    @model_validator(mode="before")
    @classmethod
    def parse_llm_settings(cls, values):
        _logger = logging.getLogger(__name__).getChild("parse_llm_settings")
        llm_list = values.get('llm', [])

        # Find all subclasses of GeneratorSettings and map them by provider
        provider_to_class = {}
        for model_cls in _iter_subclasses(GeneratorSettings):
            provider_field = model_cls.model_fields.get('provider')
            if not provider_field:
                continue
            provider_to_class[provider_field.default] = model_cls
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
        bot_info = cls.get_package_metadata().copy()
        bot_info.setdefault("BOT_ID", values.get("BOT_ID", DEFAULT_BOT_ID))
        user_agent = "Mozilla/5.0 (compatible; {BOT_ID}/{Version}; +{Home-page})".format(**bot_info)
        values.setdefault('BOT_USER_AGENT', user_agent)
        return values

    @model_validator(mode="after")
    def _stamp_identity(self) -> "Settings":
        """
        Stamp service identity fields after model fields are populated.
        """
        if not self.telemetry.service_name:
            self.telemetry.service_name = self.get_package_name()
        return self

    model_config = SettingsConfigDict(
        secrets_dir='/run/secrets' if Path('/run/secrets').exists() else None,
        yaml_file_encoding="utf-8",
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',  # If dotenv contains extra keys, ignore them
        env_nested_delimiter='__',
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
