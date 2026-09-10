"""
Length cap.

Bounds how much reader text one article can add to a prompt.
"""

import logging

from ..abc import FeedbackItem, Guardrail
from ..settings.guardrails import TruncateConfig

logger = logging.getLogger(__name__)


class TruncateGuard(Guardrail):
    """Cuts over-long messages to the configured length."""

    name = "truncate"

    def __init__(self, config: TruncateConfig) -> None:
        self.config = config

    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """Truncate each over-long message in place."""
        truncated = 0
        for item in items:
            message = item.processed.message
            if len(message) > self.config.max_message_length:
                item.processed = item.processed.model_copy(
                    update={"message": message[: self.config.max_message_length].rstrip()}
                )
                truncated += 1

        if truncated:
            logger.debug("%s: truncated %d message(s)", self.name, truncated)
        return items
