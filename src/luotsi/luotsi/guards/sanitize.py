"""
Text sanitizer.

Removes characters a reader cannot have typed on purpose, and that are useful mostly for hiding text from a
downstream matcher.
"""

import logging
import re
import unicodedata

from ..abc import FeedbackItem, Guardrail
from ..settings.guardrails import SanitizeConfig

logger = logging.getLogger(__name__)

INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
"""Zero-width, soft hyphen and bidirectional control characters."""

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
"""C0 control characters, except the tab and newline that whitespace collapsing handles."""


class SanitizeGuard(Guardrail):
    """
    Strips invisible characters and collapses whitespace.

    Normalization is NFC and case is left alone, so Finnish text keeps its ä, ö and capitalization. Guards that
    need an aggressively folded form derive it themselves from :attr:`FeedbackItem.original`.
    """

    name = "sanitize"

    def __init__(self, config: SanitizeConfig) -> None:
        self.config = config

    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """Rewrite each item's message in place on a copy of the processed feedback."""
        modified = 0
        for item in items:
            cleaned = self.clean(item.processed.message)
            if cleaned != item.processed.message:
                item.processed = item.processed.model_copy(update={"message": cleaned})
                modified += 1

        logger.debug("%s: modified %d of %d message(s)", self.name, modified, len(items))
        return items

    @staticmethod
    def clean(message: str) -> str:
        """Remove invisible and control characters, normalize to NFC and collapse whitespace."""
        message = INVISIBLE.sub("", message)
        message = CONTROL.sub("", message)
        message = unicodedata.normalize("NFC", message)
        return re.sub(r"\s+", " ", message).strip()
