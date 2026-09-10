"""
The guardrail chain.

One module per guard, and one builder that turns configuration into an ordered list of them.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..abc import Guardrail
from ..settings.guardrails import (
    GuardrailConfig,
    InjectionConfig,
    LanguageConfig,
    PiiConfig,
    SanitizeConfig,
    TruncateConfig,
)
from .injection import InjectionGuard
from .language import LanguageGuard
from .pii import PiiRedactionGuard
from .sanitize import SanitizeGuard
from .truncate import TruncateGuard

if TYPE_CHECKING:
    from meri.embedding import Embedder

__all__ = [
    "DEFAULT_CHAIN",
    "InjectionGuard",
    "LanguageGuard",
    "PiiRedactionGuard",
    "SanitizeGuard",
    "TruncateGuard",
    "build_guards",
]

logger = logging.getLogger(__name__)

DEFAULT_CHAIN: tuple[GuardrailConfig, ...] = (
    SanitizeConfig(),
    PiiConfig(),
    InjectionConfig(),
    LanguageConfig(),
    TruncateConfig(),
)
"""
The chain used when nothing is configured.

Order matters: sanitizing first gives later mutating guards clean text, redaction runs before anything keeps a
copy of the message, and truncation runs last so the cap applies to the final text.

It holds the injection guard, which needs an embedding model and somewhere to keep its artifact. It trains
that artifact itself, so the destination is all it needs — but a deployment with no embedding model still
lists its guards explicitly and leaves this one out.
"""


def build_guards(
    configs: list[GuardrailConfig] | None,
    embed: "Embedder | None" = None,
    model_name: str | None = None,
    vectors: Path | None = None,
) -> list[Guardrail]:
    """
    Build the guardrail chain.

    :param configs: Guard configurations, in the order they run. ``None`` selects :data:`DEFAULT_CHAIN`.
    :param embed: Shared embedding callable. The injection guard requires one and refuses to build without it.
    :param model_name: Name of the configured embedding model, for guards that load a model-bound artifact.
    :param vectors: Where the injection guard keeps its artifact when its own config names no path. Resolved
        by the host application, because :data:`DEFAULT_CHAIN` holds a bare ``InjectionConfig`` that no
        configuration file ever touches — without this the default chain could never name a destination.
    :raises ValueError: When a configured guard cannot run — see :class:`~.injection.InjectionGuard`.
    :return: The constructed guards.
    """
    guards: list[Guardrail] = []
    for config in configs if configs is not None else DEFAULT_CHAIN:
        match config:
            case SanitizeConfig():
                guards.append(SanitizeGuard(config))
            case PiiConfig():
                guards.append(PiiRedactionGuard(config))
            case InjectionConfig():
                guards.append(InjectionGuard(config, embed, model_name, vectors))
            case LanguageConfig():
                guards.append(LanguageGuard(config))
            case TruncateConfig():
                guards.append(TruncateGuard(config))

    logger.debug("Built guardrail chain: %s", [guard.name for guard in guards])
    return guards
