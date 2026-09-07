"""
Luotsi client.

Builds the configured feedback sources, collects their items and hands back one combined list.
"""

import logging

from .abc import Feedback, FeedbackSource
from .settings import LuotsiSettings
from .settings.source import Csv, GoogleSheets
from .sources import CsvFeedbackSource, SheetsFeedbackSource

logger = logging.getLogger(__name__)


class Luotsi:
    """Collects reader feedback from every configured source."""

    def __init__(self, settings: LuotsiSettings) -> None:
        """
        :param settings: Luotsi configuration.
        """
        self.settings = settings
        self.sources = [self._build_source(config) for config in settings.sources]
        logger.info("Initialized Luotsi with %d feedback source(s)", len(self.sources))

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
        Collect feedback from every source.

        One failing source does not stop the others.

        :return: Feedback items from all sources.
        """
        feedbacks: list[Feedback] = []
        for source in self.sources:
            try:
                feedbacks.extend(source.get_feedback())
            except Exception as e:  # noqa: BLE001
                logger.error("Error fetching feedback from %s: %s", type(source).__name__, e)

        logger.info("Fetched %d feedback item(s)", len(feedbacks))
        return feedbacks
