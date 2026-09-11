"""
Personal data redactor.

Replaces the contact details readers sometimes put in free text with a placeholder, before the text reaches a
language model. Person names are not detected — no named-entity recognition runs here — so the containment rules
around the prompt stay the real protection.
"""

import logging
import re

from ..abc import FeedbackItem, Guardrail
from ..settings.guardrails import PiiConfig

logger = logging.getLogger(__name__)

PLACEHOLDER = "[redacted]"

PATTERNS: tuple[re.Pattern[str], ...] = (
    # URLs first: they can contain an @ that would otherwise read as an email address.
    re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    # Phone numbers: an international `+` prefix or the Finnish trunk `0`, then 6-13 digits however they are grouped.
    # The prefix requirement keeps years, prices and other bare numbers out.
    re.compile(r"(?<![\w+-])(?:\+\d{1,3}|0)(?:[\s-]?\d){6,13}(?![\w-])"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b", re.IGNORECASE),
)


class PiiRedactionGuard(Guardrail):
    """Replaces emails, phone numbers, IP addresses and URLs with a placeholder."""

    name = "pii"

    def __init__(self, config: PiiConfig) -> None:
        self.config = config

    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """Redact each item's message. Counts only — redacted values never reach the log."""
        redactions = 0
        for item in items:
            message, count = self.redact(item.processed.message)
            if count:
                item.processed = item.processed.model_copy(update={"message": message})
                redactions += count

        logger.debug("%s: redacted %d value(s) across %d message(s)", self.name, redactions, len(items))
        return items

    @staticmethod
    def redact(message: str) -> tuple[str, int]:
        """
        Replace every known contact detail in the message.

        :return: The redacted message and the number of replacements made.
        """
        total = 0
        for pattern in PATTERNS:
            message, count = pattern.subn(PLACEHOLDER, message)
            total += count
        return message, total
