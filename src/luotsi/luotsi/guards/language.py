"""
Language filter.

Keeps feedback in the languages Meri generates titles in. Detection runs on the original message: redaction
placeholders and stripped characters both hurt detection accuracy.
"""

import logging

from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

from ..abc import FeedbackItem, Guardrail
from ..settings.guardrails import LanguageConfig

logger = logging.getLogger(__name__)

DetectorFactory.seed = 0
"""langdetect samples at random; a fixed seed makes a message's verdict reproducible."""

MIN_LENGTH = 25
"""Below this, langdetect guesses more than it detects, so the message counts as unknown."""


class LanguageGuard(Guardrail):
    """Drops messages written in a language Meri does not generate titles in."""

    name = "language"

    def __init__(self, config: LanguageConfig) -> None:
        self.config = config
        self.languages = {code.lower().strip() for code in config.languages}

    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """Keep items whose detected language is allowed, plus unknown ones when configured to."""
        if not self.languages:
            return items

        kept = [item for item in items if self._is_allowed(item)]

        dropped = len(items) - len(kept)
        if dropped:
            logger.info("%s: dropped %d message(s) in a language outside %r", self.name, dropped, self.languages)
        return kept

    def _is_allowed(self, item: FeedbackItem) -> bool:
        """Detect the language of one message. Short and undetectable messages count as unknown."""
        message = item.original.message.strip()
        if len(message) < MIN_LENGTH:
            return self.config.allow_unknown

        try:
            return detect(message).lower().strip() in self.languages
        except LangDetectException:
            return self.config.allow_unknown
