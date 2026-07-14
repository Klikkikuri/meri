"""
Google Sheets Feedback Source for Luotsi.

Fetches user feedback data using the Google Sheets Visualization API (gviz/tq)
to retrieve CSV responses directly. Normalizes headers to match the target schema.
"""

import csv
import logging
import requests
from ..abc import Feedback, FeedbackSource
from ..config import LuotsiFeedbackSourceSheets
from .csv_helper import parse_csv_rows

logger = logging.getLogger(__name__)

class SheetsFeedbackSource(FeedbackSource):
    """
    Feedback source that extracts feedback entries from a public/accessible Google Sheet.

    Uses the gviz/tq endpoint to fetch values as a CSV table, bypassing redirect structures.
    """
    def __init__(self, config: LuotsiFeedbackSourceSheets) -> None:
        """
        Initialize SheetsFeedbackSource.

        Args:
            config: Sheets source configuration containing spreadsheet ID and optional worksheet name.
        """
        self.config = config

    def get_feedback(self, signature: str | None = None) -> list[Feedback]:
        """
        Fetch feedback items from Google Sheet.

        Args:
            signature: Optional URL hash signature to filter feedback.

        Returns:
            A list of validated Feedback items.
        """
        # Use Google Visualization API (gviz/tq) endpoint to get CSV
        url = f"https://docs.google.com/spreadsheets/d/{self.config.spreadsheet_id}/gviz/tq?tqx=out:csv"
        if self.config.worksheet:
            url += f"&sheet={self.config.worksheet}"

        try:
            logger.info("Fetching feedback from Google Sheets gviz/tq URL: %s", url)
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch Google Sheet feedback from %s: %s", url, e)
            return []

        # Parse CSV
        lines = response.text.splitlines()
        if not lines:
            return []

        reader = csv.DictReader(lines)
        return parse_csv_rows(reader, signature=signature)
