"""
Luotsi client.

Builds the configured feedback sources, collects their items and hands back one combined list.
"""

import logging

from .abc import Feedback, FeedbackItem, FeedbackSource
from .embeddings import load_embedder
from .guards import build_guards
from .settings import LuotsiSettings
from .settings.source import Csv, GoogleSheets
from .sources import CsvFeedbackSource, SheetsFeedbackSource

logger = logging.getLogger(__name__)


class Luotsi:
    """Collects reader feedback from every configured source and sanitizes it before anyone else sees it."""

    def __init__(self, settings: LuotsiSettings) -> None:
        """
        :param settings: Luotsi configuration.
        """
        self.settings = settings
        self.sources = [self._build_source(config) for config in settings.sources]
        # A configured model is a hard requirement: load errors propagate rather than degrade the guard silently.
        embed = load_embedder(settings.embedding_model) if settings.embedding_model else None
        self.guards = build_guards(settings.guardrails, embed=embed)
        logger.info(
            "Initialized Luotsi with %d feedback source(s) and %d guard(s)", len(self.sources), len(self.guards)
        )

    @staticmethod
    def _build_source(config: Csv | GoogleSheets) -> FeedbackSource:
        """Instantiate the source class matching a source configuration."""
        match config:
            case Csv():
                return CsvFeedbackSource(config)
            case GoogleSheets():
                return SheetsFeedbackSource(config)

    def get_feedback(self) -> list[Feedback]:
        """
        Collect feedback from every source and run it through the guardrail chain.

        One failing source does not stop the others, and one guard failing mid-run does not discard the batch.

        :return: Sanitized feedback items.
        """
        feedbacks: list[Feedback] = []
        for source in self.sources:
            try:
                feedbacks.extend(source.get_feedback())
            except Exception as e:  # noqa: BLE001
                logger.error("Error fetching feedback from %s: %s", type(source).__name__, e)

        items = [FeedbackItem(original=feedback, processed=feedback) for feedback in feedbacks]
        for guard in self.guards:
            try:
                items = guard.run(items)
            except Exception as e:  # noqa: BLE001
                logger.error("Guard %r failed and was skipped: %s", guard.name, e)

        logger.info("Fetched %d feedback item(s), %d survived the guardrail chain", len(feedbacks), len(items))
        return [item.processed for item in items]
