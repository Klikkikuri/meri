"""
Configuration schemas for the guardrail chain.

Every guard is selected, ordered and tuned from configuration. ``LuotsiSettings.guardrails`` set to ``None`` runs
the default chain in the default order.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

DEFAULT_CLUSTER_THRESHOLD = 0.85
"""
Similarity at which two exemplars of one label share a centroid.

Lives here rather than in the trainer because it is a configuration default that the trainer reads, and
because `guards` may import `settings` while the reverse closes a cycle.
"""


def _default_exemplars() -> list[Path]:
    """
    The labeled exemplar files shipped with Luotsi.

    Imported inside the function, not at module level: `guards` imports THIS module
    (:mod:`luotsi.guards` pulls in the configuration classes), so a top-level import would close the cycle.
    It survives today only because `guards.labeled` happens to depend on nothing in its own package
    `__init__`, which is an accident rather than a guarantee.
    """
    from ..guards.labeled import packaged_exemplars

    return packaged_exemplars()


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
    vectors: Path | None = Field(
        default=None,
        description="Where the trained centroid artifact is kept. The guard trains it when it is missing or "
        "its inputs have changed. Overrides the destination the host application resolves.",
    )
    data: list[Path] = Field(
        default_factory=_default_exemplars,
        description="Labeled exemplar files to train from. Defaults to the catalog shipped with Luotsi; "
        "setting this REPLACES it rather than adding to it, exactly as `train-guard --data` does.",
    )
    cluster_threshold: float = Field(
        default=DEFAULT_CLUSTER_THRESHOLD,
        gt=0.0,
        le=1.0,
        description="Similarity at which two exemplars of one label share a centroid. Changing it retrains.",
    )
    floor: float = Field(
        default=0.60,
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
