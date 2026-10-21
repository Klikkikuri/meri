"""
Configuration settings.
=======================

This module defines the configuration settings for the Klikkikuri 🦈 service.

Order of precedence:
    1. Environment variables
    2. `.env` file
    3. Secrets directory (e.g. `/run/secrets`).
    4. YAML configuration files, each one's top-level keys replacing those of the ones before it:
        - Bundled defaults: `packages/rahti/config.yaml`
        - Devcontainer user settings: `/app/config.yaml`
        - Instance folder settings: `/app/instance/config.yaml`
        - Docker settings: `/config/config.yaml`
        - Local settings: `./config.yaml`
        - System wide settings ($XDG_CONFIG_DIRS / site_config_dir)
        - User defined settings ($XDG_CONFIG_HOME / user_config_dir)

    The merge is shallow: a file that defines `llm:` replaces the whole list, it does not extend it.

"""
import os
from importlib.util import find_spec
from pathlib import Path
from typing import cast

# Ugly duckling hack – load .env before initializing settings, to ensure that environment variables are available
from dotenv import load_dotenv
from niitti import SettingsProxy, get_logger
from niitti.settings.logging import LoggingSettings
from niitti.settings.sentry import SentrySettings
from niitti.settings.settings import ServiceSettings, lint_yaml_settings_files
from niitti.settings.telemetry import TelemetrySettings
from platformdirs import user_config_dir
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from meri.luotsi.settings import LuotsiSettings

from .const import (
    DEFAULT_BOT_ID,
    DEFAULT_BOT_USER_AGENT,
    PKG_NAME,
)
from .embedding import EmbeddingSettings, LocalEmbeddingSettings
from .llms import (
    LLMSetting,
    detect_generators,
)
from .newssources import NewsSource
from .pipelines import PipelineSettings, UnknownLLMError
from .rahti import RahtiSettings
from .sulku import SulkuSettings

load_dotenv()

logger = get_logger(__name__)

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None

_otel_available: bool = find_spec("opentelemetry.exporter") is not None

# Default sources, LLMs and blacklist shipped with the Rahti submodule. Lowest-precedence config file.
_BUNDLED_CONFIG = Path("packages/rahti/config.yaml").resolve()

# The official provider manifest in the Rahti submodule. It links the Suola rules and the Rahti data.
_BUNDLED_MANIFEST = Path("packages/rahti/manifest.json").resolve()


class SkipProcessingSettings(BaseModel):
    """
    Settings for skipping article title processing.
    """

    labels: list[str] = Field(
        default_factory=lambda: ["paywalled=true", "type=video"],
        description="List of label selectors (e.g. 'paywalled=true', 'type=video', 'article-type = opinion') that skip title generation and are stored in Rahti without processing.",
    )

    @field_validator("labels")
    @classmethod
    def validate_label_selectors(cls, v: list[str]) -> list[str]:
        from meri.labels import LabelSelector

        for selector_str in v:
            LabelSelector.parse(selector_str)
        return v


class Settings(ServiceSettings):
    logging: LoggingSettings = Field(
        default_factory=LoggingSettings,
        description="Logging settings.",
    )
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
        DEFAULT_BOT_USER_AGENT,
        description="User agent as f-string template for requests. Can be formatted with "
        "package metadata, and `BOT_ID`.",
    )
    LLM_USER_AGENT: str | None = Field(
        None,
        description="User agent as f-string template for LLM requests. Defaults to `BOT_USER_AGENT`. "
        "Can be formatted with package metadata, and `BOT_ID`.",
    )
    REQUESTS_CACHE: bool = Field(_requests_cache_available, description="Enable requests cache.")
    MAX_WORKERS: int = Field(3, description="Maximum number of worker threads for processing articles.")

    PROMPT_DIR: Path = Field(Path(user_config_dir(PKG_NAME), "prompts"), description="Directory to store prompt templates.")

    llm: list[LLMSetting] = Field(default_factory=list, description="List of language models to use.")
    pipelines: dict[str, PipelineSettings] = Field(
        default_factory=dict,
        description="Pipeline definitions, keyed by pipeline name. A pipeline with no entry uses the defaults.",
    )

    sources: list[NewsSource] = Field(default_factory=list, description="List of news sources to scrape.")

    manifest: str | None = Field(
        str(_BUNDLED_MANIFEST) if _BUNDLED_MANIFEST.exists() else None,
        description="Location of the provider manifest: a path, a `file://` URL, a GitHub URL or an `https://` "
        "URL. Meri reads its Suola rules from it. If empty, the rules built into the Suola module are used.",
    )

    @classmethod
    def get_default_config_locations(cls) -> list[Path]:
        """
        Prepend the Rahti submodule's bundled defaults to niitti's standard search path.

        Order is ascending precedence — `YamlConfigSettingsSource` shallow-merges the list with
        `dict.update`, so the last file to define a top-level key wins outright. The bundled
        config therefore goes first: it supplies the default sources and LLMs, and everything an
        operator writes in `instance/config.yaml` (or any later location) replaces it.
        """
        return lint_yaml_settings_files([_BUNDLED_CONFIG]) + super().get_default_config_locations()

    @field_validator("manifest")
    @classmethod
    def _empty_manifest(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None

    @model_validator(mode="before")
    @classmethod
    def _reject_suola_rules(cls, values):
        """
        Refuse the removed `suola_rules` setting.

        Extra keys are ignored, so the old key would be dropped silently and signatures would change with no error.
        The environment is checked on its own, because the environment source reads declared fields only.
        """
        if any(key.lower() == "suola_rules" for key in [*values, *os.environ]):
            raise ValueError(
                "The `suola_rules` setting is removed. Set `manifest` to a provider manifest that links the rules "
                "as a `suola-rules` resource, or leave `manifest` empty to use the built-in rules."
            )
        return values

    url_blacklist: list[str] = Field(default_factory=list, description="List of URL patterns to ignore.")
    """
    URL patterns to ignore. Can be substrings or regex patterns (if enclosed in slashes, e.g. `/pattern/`).

    This is to limit the scope of scraping to relevant sites, and not to log suola errors for irrelevant sites.
    """

    skip_processing: SkipProcessingSettings = Field(
        default_factory=SkipProcessingSettings,
        description="Settings for skipping article title processing.",
    )

    rahti: RahtiSettings = Field(
        default_factory=lambda: RahtiSettings(),  # type: ignore
        description="Rahti storage settings.",
    )

    sulku: SulkuSettings = Field(
        default_factory=lambda: SulkuSettings(),  # type: ignore
        description="Sulku AI-detection service settings.",
    )

    luotsi: LuotsiSettings | None = Field(
        default=None,
        description="Luotsi reader feedback settings. Omit to run without reader feedback.",
    )

    embedding: EmbeddingSettings | None = Field(
        default_factory=LocalEmbeddingSettings,
        description="The one embedding model shared by reader feedback and the title guards. Omit for the "
        "default model; set to null to run without one.",
    )

    @model_validator(mode="before")
    @classmethod
    def parse_llm_settings(cls, values):
        """
        Fill in `llm:` from the environment when the configuration names none.

        The entries themselves need no help here: `LLMSetting` is a discriminated union, so pydantic maps
        `provider:` to its settings class and reports an unknown one against the valid tags.
        """
        if not values.get("llm"):
            values["llm"] = detect_generators(values)

        return values

    @model_validator(mode="after")
    def _check_pipeline_llms(self) -> "Settings":
        """
        Check that every LLM a pipeline names is configured.

        Must run after validation, not before: `parse_llm_settings` is a before-validator, and only afterwards
        does `self.llm` hold objects with a `.name`. A bad name fails here, at `bootstrap.setup()`, rather than
        at the first generation.
        """
        known = [llm.name for llm in self.llm]

        duplicates = sorted({name for name in known if known.count(name) > 1})
        if duplicates:
            raise UnknownLLMError(f"Duplicate LLM name(s) in `llm:`: {', '.join(duplicates)}. Names are keys.")

        for pipeline, definition in self.pipelines.items():
            for name in definition.llm:
                if name not in known:
                    raise UnknownLLMError(
                        f"Pipeline {pipeline!r} names LLM {name!r}, which `llm:` does not configure. "
                        f"Configured: {', '.join(known) or '(none)'}."
                    )

        return self

    @model_validator(mode="after")
    def _compute_user_agent(self) -> "Settings":
        """
        format the BOT_USER_AGENT string with package metadata and BOT_ID after model fields are populated.
        """
        bot_info = self.get_package_metadata().copy()
        bot_info.setdefault("BOT_ID", self.BOT_ID)

        self.BOT_USER_AGENT = str(self.BOT_USER_AGENT).format(**bot_info)
        # One identity is enough. An operator who does not need a separate LLM user agent gets the bot's.
        self.LLM_USER_AGENT = (
            str(self.LLM_USER_AGENT).format(**bot_info) if self.LLM_USER_AGENT else self.BOT_USER_AGENT
        )
        return self

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


_active_settings: Settings | None = None


def get_settings() -> Settings | None:
    """
    Get the currently active application Settings instance, or None if outside app context.
    """
    return _active_settings


def set_active_settings(instance: Settings | None) -> None:
    """
    Set the active application Settings instance.
    """
    global _active_settings
    _active_settings = instance


def clear_settings() -> None:
    """
    Clear the currently active application Settings instance.
    """
    global _active_settings
    _active_settings = None


settings = cast(Settings, SettingsProxy(get_settings))
