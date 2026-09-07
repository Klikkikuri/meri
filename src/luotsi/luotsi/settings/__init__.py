"""Configuration schema for Luotsi."""

from pydantic import BaseModel, Field

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
