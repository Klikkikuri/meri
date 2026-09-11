"""Configuration schema for Luotsi."""

from pathlib import Path

from niitti.paths import data_dir
from pydantic import BaseModel, ConfigDict, Field, field_validator

from meri.settings.const import APP_NAME

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


def default_vectors() -> Path:
    """
    Where the injection guard keeps its trained artifact when no guard names a path of its own.

    It lands in the data directory, which a container mounts as a persistent volume, so a rebuilt image with new
    exemplars retrains once and keeps the result.

    :return: `<data dir>/guard-vectors.json`. Neither it nor its parent need exist yet.
    """
    return data_dir(APP_NAME) / "guard-vectors.json"


class LuotsiSettings(BaseModel):
    """
    Reader feedback settings.

    Sources default to empty so that a host application can embed the model without configuring Luotsi. The
    embedding model is not named here: `embedding:` names the one model every embedding consumer shares, and
    the client receives it as a callable. Extra keys are rejected so a stale `embedding_model` fails loudly.
    """

    model_config = ConfigDict(extra="forbid")

    sources: list[FeedbackSourceConfig] = Field(default_factory=list, description="Feedback sources to pull from.")

    guardrails: list[GuardrailConfig] | None = Field(
        default=None,
        description="Guards to run, in order. Omit for the default chain: sanitize, pii, injection, language, "
        "truncate.",
    )

    guard_vectors: Path = Field(
        default_factory=default_vectors,
        description="Where the injection guard keeps its trained artifact, when no guard names one of its own. "
        "Defaults to the data directory, which a container mounts as a persistent volume.",
    )

    clustering: ClusteringSettings = Field(
        default_factory=ClusteringSettings, description="Message consolidation tuning."
    )

    max_messages_per_article: int = Field(
        default=3, ge=0, description="Most consolidated message groups to pass on for one article."
    )

    @field_validator("guard_vectors", mode="before")
    @classmethod
    def _vectors_default_on_null(cls, value: Path | None) -> Path:
        """An explicit null is not a mode here: the guard needs somewhere to write, so null means the default."""
        return default_vectors() if value is None else value
