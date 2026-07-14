"""
Google Sheets Feedback Source for Luotsi.

Fetches user feedback data using the Google Sheets Visualization API (gviz/tq)
to retrieve CSV responses directly. Normalizes headers to match the target schema.
"""

import csv
import logging
from concurrent.futures import Future, ThreadPoolExecutor
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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
        Initialize SheetsFeedbackSource and start background data fetching.

        :param config: Sheets source configuration containing spreadsheet ID and optional worksheet name.
        """
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._future: Future[list[Feedback]] = self._executor.submit(self._fetch_and_parse)
        # Prevent new tasks but let the submitted fetch finish.
        self._executor.shutdown(wait=False)

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _fetch_csv_with_retry(self, url: str) -> str:
        """
        Fetch CSV data from the URL with retries for temporary network issues.

        :param url: URL to fetch CSV from.
        :raises requests.RequestException: If the requests fails.
        :return: CSV text.
        """
        logger.info("Fetching feedback from Google Sheets gviz/tq URL: %s", url)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def _fetch_and_parse(self) -> list[Feedback]:
        """
        Fetch Google Sheets CSV data in the background and parse it.

        :return: A list of parsed Feedback items.
        """
        url = f"https://docs.google.com/spreadsheets/d/{self.config.spreadsheet_id}/gviz/tq?tqx=out:csv"
        if self.config.worksheet:
            url += f"&sheet={self.config.worksheet}"

        try:
            csv_text = self._fetch_csv_with_retry(url)
            lines = csv_text.splitlines()
            if not lines:
                return []
            reader = csv.DictReader(lines)
            return parse_csv_rows(reader)
        except Exception as e:
            logger.error("Failed to fetch Google Sheet feedback from %s: %s", url, e)
            return []

    def get_feedback(self, signature: str | None = None) -> list[Feedback]:
        """
        Fetch feedback items from Google Sheet.

        Blocks if background thread is still running.

        :param signature: Optional URL hash signature to filter feedback.
        :return: A list of validated Feedback items.
        """
        feedbacks = self._future.result()

        if signature is not None:
            return [fb for fb in feedbacks if fb.url_sign == signature]
        return feedbacks
