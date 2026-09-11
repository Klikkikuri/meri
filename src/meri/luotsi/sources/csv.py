"""
Local CSV file feedback source.

Reads a tabular export of the feedback form from disk. Useful offline and in tests.
"""

import csv
import logging
import os

from ..abc import Feedback, FeedbackSource
from ..settings.source import Csv
from .csv_helper import parse_rows

logger = logging.getLogger(__name__)


class CsvFeedbackSource(FeedbackSource):
    """Feedback source that reads rows from a local CSV file."""

    def __init__(self, config: Csv) -> None:
        """
        :param config: CSV source configuration holding the local file path.
        """
        self.config = config

    def get_feedback(self) -> list[Feedback]:
        """
        Read and parse the configured CSV file.

        :return: Validated feedback items, or an empty list when the file is missing or unreadable.
        """
        path = self.config.path
        if not os.path.exists(path):
            logger.warning("Local CSV feedback file does not exist: %s", path)
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                return parse_rows(csv.DictReader(f), self.config)
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to read local CSV feedback from %s: %s", path, e)
            return []
