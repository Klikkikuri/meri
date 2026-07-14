"""
Local CSV File Feedback Source for Luotsi.

Loads feedback rows from a local CSV file path. Useful for offline, local, or test usage.
"""

import csv
import logging
import os
from ..abc import Feedback, FeedbackSource
from ..config import LuotsiFeedbackSourceCsv
from .csv_helper import parse_csv_rows

logger = logging.getLogger(__name__)

class CsvFeedbackSource(FeedbackSource):
    """
    Feedback source that extracts feedback entries from a local CSV file.

    Reads file rows and delegates parsing to csv_helper.
    """
    def __init__(self, config: LuotsiFeedbackSourceCsv) -> None:
        """
        Initialize CsvFeedbackSource.

        Args:
            config: CSV source configuration containing the local file path.
        """
        self.config = config

    def get_feedback(self, signature: str | None = None) -> list[Feedback]:
        """
        Fetch feedback items from the local CSV file.

        Args:
            signature: Optional URL hash signature to filter feedback.

        Returns:
            A list of validated Feedback items.
        """
        path = self.config.path
        if not os.path.exists(path):
            logger.warning("Local CSV feedback file does not exist: %s", path)
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return parse_csv_rows(list(reader), signature=signature)
        except Exception as e:
            logger.error("Failed to read local CSV feedback from %s: %s", path, e)
            return []
