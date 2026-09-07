"""
Configuration schemas for the guardrail chain.

Every guard is selected, ordered and tuned from configuration. ``LuotsiSettings.guardrails`` set to ``None`` runs
the default chain in the default order.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class _Guardrail(BaseModel):
    """Fields shared by every guardrail configuration."""


class SanitizeConfig(_Guardrail):
    """Configuration for the non-destructive text sanitizer."""

    type: Literal["sanitize"] = "sanitize"


class PiiConfig(_Guardrail):
    """Configuration for the personal data redactor."""

    type: Literal["pii"] = "pii"


class InjectionConfig(_Guardrail):
    """Configuration for the prompt injection guard."""

    type: Literal["injection"] = "injection"
    blocklist: list[str] = Field(
        default_factory=list, description="Extra literal phrases to drop, on top of the built-in indicators."
    )
    vectors: Path | None = Field(
        default=None, description="Trained centroid artifact. Overrides the one packaged with Luotsi."
    )
    floor: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Minimum similarity to an injection centroid before a message can be dropped.",
    )
    margin: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="How far the injection similarity must exceed the closest benign centroid to drop a message.",
    )


class LanguageConfig(_Guardrail):
    """Configuration for the language filter."""

    type: Literal["language"] = "language"
    languages: list[str] = Field(
        default_factory=lambda: ["fi", "en"], description="Language codes Meri generates titles in."
    )
    allow_unknown: bool = Field(
        default=True, description="Keep messages whose language cannot be detected. Short messages often cannot."
    )


class TruncateConfig(_Guardrail):
    """Configuration for the length cap."""

    type: Literal["truncate"] = "truncate"
    max_message_length: int = Field(default=500, gt=0, description="Longest message, in characters, to keep.")


GuardrailConfig = Annotated[
    SanitizeConfig | PiiConfig | InjectionConfig | LanguageConfig | TruncateConfig,
    Field(discriminator="type"),
]
