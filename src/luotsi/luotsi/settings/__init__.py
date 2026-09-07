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
        description="Local Model2Vec model directory, shared by message consolidation and the injection guard's "
        "centroid tier. Provision it with `luotsi download-model`. Omit to run both in their built-in mode.",
    )

    clustering: ClusteringSettings = Field(
        default_factory=ClusteringSettings, description="Message consolidation tuning."
    )

    max_messages_per_article: int = Field(
        default=3, ge=0, description="Most consolidated message groups to pass on for one article."
    )

    @field_validator("embedding_model")
    @classmethod
    def _model_must_exist(cls, value: Path | None) -> Path | None:
        """
        A configured model is a hard requirement.

        Fail here, at configuration load, rather than degrading silently into the weaker built-in mode at run time.
        """
        if value is not None and not value.exists():
            raise ValueError(f"embedding_model path does not exist: {value}")
        return value
