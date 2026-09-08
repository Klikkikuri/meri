"""Configuration schema for Luotsi."""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .clustering import ClusteringSettings
from .guardrails import (
    GuardrailConfig,
    InjectionConfig,
    LanguageConfig,
    PiiConfig,
    SanitizeConfig,
    TruncateConfig,
)
from .source import Csv, FeedbackSourceConfig, GoogleSheets

__all__ = [
    "ClusteringSettings",
    "Csv",
    "FeedbackSourceConfig",
    "GoogleSheets",
    "GuardrailConfig",
    "InjectionConfig",
    "LanguageConfig",
    "LuotsiSettings",
    "PiiConfig",
    "SanitizeConfig",
    "TruncateConfig",
]


class LuotsiSettings(BaseModel):
    """
    Reader feedback settings.

    Sources default to empty so that a host application can embed the model without configuring Luotsi.
    """

    sources: list[FeedbackSourceConfig] = Field(default_factory=list, description="Feedback sources to pull from.")

    guardrails: list[GuardrailConfig] | None = Field(
        default=None,
        description="Guards to run, in order. Omit for the default chain: sanitize, pii, injection, language, "
        "truncate.",
    )

    embedding_model: Path | None = Field(
        default=None,
        description="Directory of the Model2Vec model shared by message consolidation and the injection guard's "
        "centroid tier. The host application resolves it — in Meri, `luotsi.embedding_model` in `config.yaml` "
        "names a hub model or a directory, and `meri feedback download-model` fills it. None runs both in "
        "their weaker built-in mode.",
    )

    clustering: ClusteringSettings = Field(
        default_factory=ClusteringSettings, description="Message consolidation tuning."
    )

    max_messages_per_article: int = Field(
        default=3, ge=0, description="Most consolidated message groups to pass on for one article."
    )

    @field_validator("embedding_model")
    @classmethod
    def _model_must_be_a_directory(cls, value: Path | None) -> Path | None:
        """
        A configured directory need not hold a model yet — it need only be able to.

        `meri feedback download-model` creates and fills it, so on a cold start it is absent, and the host
        application decides where it lands. A path that exists as something other than a directory can never
        work, and this is the earliest place to say so. Whether the directory really holds a loadable model is
        settled when it is loaded, at the start of a run, so a configured model still never degrades silently
        into the built-in mode.
        """
        if value is not None and value.expanduser().exists() and not value.expanduser().is_dir():
            raise ValueError(f"embedding_model is not a directory: {value}")
        return value
