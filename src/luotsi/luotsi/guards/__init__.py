"""
The guardrail chain.

One module per guard, and one builder that turns configuration into an ordered list of them.
"""

import logging
from collections.abc import Callable

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

__all__ = [
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
"""


def build_guards(
    configs: list[GuardrailConfig] | None,
    embed: Callable[[str], "object"] | None = None,
) -> list[Guardrail]:
    """
    Build the guardrail chain.

    :param configs: Guard configurations, in the order they run. ``None`` selects :data:`DEFAULT_CHAIN`.
    :param embed: Shared embedding callable. Guards with a semantic tier stay on their literal tier without it.
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
                guards.append(InjectionGuard(config))
            case LanguageConfig():
                guards.append(LanguageGuard(config))
            case TruncateConfig():
                guards.append(TruncateGuard(config))

    logger.debug("Built guardrail chain: %s", [guard.name for guard in guards])
    return guards
