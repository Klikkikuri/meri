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
from importlib.util import find_spec
from pathlib import Path
from typing import cast

# Ugly duckling hack – load .env before initializing settings, to ensure that environment variables are available
from dotenv import load_dotenv
from luotsi.settings import LuotsiSettings
from niitti import SettingsProxy, get_logger
from niitti.settings.logging import LoggingSettings
from niitti.settings.sentry import SentrySettings
from niitti.settings.settings import Settings as NiittiSettings
from niitti.settings.settings import lint_yaml_settings_files
from niitti.settings.telemetry import TelemetrySettings
from platformdirs import user_config_dir
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from .const import (
    DEFAULT_BOT_ID,
    DEFAULT_BOT_USER_AGENT,
    PKG_NAME,
)
from .llms import (
    LLMSetting,
    detect_generators,
)
from .luotsi import DEFAULT_EMBEDDING_MODEL, default_vectors, hub_id_of, model_dir
from .newssources import NewsSource
from .pipelines import PipelineSettings, UnknownLLMError
from .rahti import RahtiFileSettings, RahtiSettings
from .sulku import SulkuSettings

load_dotenv()

logger = get_logger(__name__)

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None

_otel_available: bool = find_spec("opentelemetry.exporter") is not None

# Compiled Suola rules from the monorepo, built by `make rules` in the suola checkout.
_suola_rules = Path("packages/suola/build/rules.json").resolve()

# Default sources, LLMs and blacklist shipped with the Rahti submodule. Lowest-precedence config file.
_BUNDLED_CONFIG = Path("packages/rahti/config.yaml").resolve()


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


class Settings(NiittiSettings):
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
    REQUESTS_CACHE: bool = Field(_requests_cache_available, description="Enable requests cache.")
    MAX_WORKERS: int = Field(3, description="Maximum number of worker threads for processing articles.")

    PROMPT_DIR: Path = Field(Path(user_config_dir(PKG_NAME), "prompts"), description="Directory to store prompt templates.")

    llm: list[LLMSetting] = Field(default_factory=list, description="List of language models to use.")
    pipelines: dict[str, PipelineSettings] = Field(
        default_factory=dict,
        description="Pipeline definitions, keyed by pipeline name. A pipeline with no entry uses the defaults.",
    )

    sources: list[NewsSource] = Field(default_factory=list, description="List of news sources to scrape.")

    suola_rules: str | None = Field(
        str(_suola_rules) if _suola_rules.exists() else None,
        description="Location of the compiled Suola JSON rules: an `http(s)://` URL, a `file://` URL or a "
        "filesystem path. If empty, the rules built into the Suola module are used.",
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

    @field_validator("suola_rules")
    @classmethod
    def validate_suola_rules(cls, v: str | None) -> str | None:
        from meri.suola import assert_json_rules

        v = v.strip() if v else ""
        if not v:
            return None

        # Fail here rather than deep in the Wasm module, so a stale YAML location names the setting it came from.
        assert_json_rules(v)
        return v

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
        default_factory=lambda: RahtiFileSettings(),  # type: ignore
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

    @staticmethod
    def _identity_of_model(resolved: Path | None) -> str | None:
        """
        What the guard's artifact records as the model that produced it.

        The hub identifier when the model came from the hub, so `minishlab/potion-multilingual-128M` and
        `otherorg/potion-multilingual-128M` are distinguishable — the bare directory name is not, and neither
        is the dimension, so the guard would score one model's centroids against the other's embeddings.
        A model provisioned by other means keeps its directory name rather than its absolute path, which would
        be machine-specific and would make merely moving an identical model read as a different one.
        """
        return None if resolved is None else (hub_id_of(resolved) or resolved.name)

    @field_validator("luotsi", mode="before")
    @classmethod
    def resolve_embedding_model(cls, value):
        """
        Turn `luotsi.embedding_model` into the directory Luotsi loads from.

        Luotsi takes a directory and carries no default, so the model is named here: an unset key means
        `DEFAULT_EMBEDDING_MODEL`, a hub identifier resolves under the data directory, and an explicit path is
        left alone. Setting the key to null still selects the built-in mode, which is why an unset key and a
        null one are told apart rather than both falling back to the default.
        """
        if value is None:
            return value

        # A configuration file gives a mapping; a caller constructing Settings in code gives the model.
        if isinstance(value, LuotsiSettings):
            update: dict[str, Path | str | None] = {}

            resolved = value.embedding_model
            if "embedding_model" not in value.model_fields_set:
                resolved = model_dir(DEFAULT_EMBEDDING_MODEL)
                update["embedding_model"] = resolved

            # Not `model_fields_set`, unlike the model above: an explicit null is a MODE for `embedding_model`
            # but means nothing for a destination, and honouring it here would leave a guard built in code with
            # nowhere to read from — a difference the dict branch below does not make either.
            if value.guard_vectors is None:
                update["guard_vectors"] = default_vectors()

            if value.embedding_model_id is None:
                update["embedding_model_id"] = cls._identity_of_model(resolved)

            return value.model_copy(update=update) if update else value

        if not isinstance(value, dict):
            return value

        model = value.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
        resolved = model_dir(str(model)) if model else None
        return {
            **value,
            "embedding_model": resolved,
            # `minishlab/potion-multilingual-128M` rather than the bare `potion-multilingual-128M`, which
            # cannot tell two same-named models from different orgs apart. A model provisioned by other means
            # has no hub identity, so it keeps the directory name — not the absolute path, which would be
            # machine-specific and would make merely MOVING an identical model read as a different one.
            "embedding_model_id": value.get("embedding_model_id") or cls._identity_of_model(resolved),
            # Unlike the model, an explicit null is not a mode: the guard needs somewhere to write, and only
            # a guard naming its own `vectors` overrides where.
            "guard_vectors": value.get("guard_vectors") or default_vectors(),
        }

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
