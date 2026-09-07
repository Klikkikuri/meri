"""
Prompt injection guard.

Drops feedback that tries to instruct the language model instead of commenting on a title. Both tiers key off
:attr:`FeedbackItem.original` so that the sanitizer and the redactor cannot remove the evidence they look for.
"""

import logging
import unicodedata

from ..abc import FeedbackItem, Guardrail
from ..settings.guardrails import InjectionConfig
from .sanitize import INVISIBLE

logger = logging.getLogger(__name__)

INDICATORS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "ignore the above",
    "system prompt",
    "override settings",
    "you are now a",
    "act as if you",
    "print the secret",
    "reveal your instructions",
    "unohda aiemmat ohjeet",
    "sivuuta aiemmat ohjeet",
    "olet nyt",
    "kerro ohjeesi",
)
"""Literal phrases that no reader comments on a headline with."""


def blocklist_key(message: str) -> str:
    """
    Fold a message into the form the blocklist matches against.

    Drops invisible characters, decomposes to NFKD, drops combining marks and case-folds, so zero-width padding,
    accents, homoglyph decompositions and alternating case do not hide a known phrase. The result is a matching key
    only — it never replaces the message itself.
    """
    decomposed = unicodedata.normalize("NFKD", INVISIBLE.sub("", message))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


class InjectionGuard(Guardrail):
    """Drops messages that match a known injection phrase."""

    name = "injection"

    def __init__(self, config: InjectionConfig) -> None:
        self.config = config
        self.blocklist = tuple(blocklist_key(phrase) for phrase in (*INDICATORS, *config.blocklist))

    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """
        Keep only the items that no tier flags.

        A flagged item is removed whole, so it contributes neither a message nor a vote nor a regeneration trigger.
        """
        kept = [item for item in items if not self._is_injection(item)]

        dropped = len(items) - len(kept)
        if dropped:
            logger.warning("%s: dropped %d message(s) matching an injection phrase", self.name, dropped)
        return kept

    def _is_injection(self, item: FeedbackItem) -> bool:
        """Test one item against the blocklist tier. Message bodies never reach the log."""
        key = blocklist_key(item.original.message)
        return any(phrase in key for phrase in self.blocklist)
