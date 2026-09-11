"""
Google Sheets feedback source.

Fetches the responses sheet through the Visualization API (``gviz/tq``), which returns the sheet as CSV without
needing credentials for a publicly readable sheet.
"""

import csv
import logging
from urllib.parse import quote

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..abc import Feedback, FeedbackSource
from ..settings.source import GoogleSheets
from .csv_helper import parse_rows

logger = logging.getLogger(__name__)


class SheetsFeedbackSource(FeedbackSource):
    """Feedback source that reads a public Google Sheet as CSV."""

    def __init__(self, config: GoogleSheets) -> None:
        """
        :param config: Sheets source configuration holding the spreadsheet ID and optional worksheet name.
        """
        self.config = config

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _fetch_csv(self, url: str) -> str:
        """
        Fetch the sheet as CSV, retrying temporary network failures.

        :param url: The ``gviz/tq`` URL to read.
        :raises requests.RequestException: When every attempt fails.
        :return: CSV text.
        """
        logger.info("Fetching feedback from Google Sheets")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def get_feedback(self) -> list[Feedback]:
        """
        Fetch and parse the configured sheet.

        :return: Validated feedback items, or an empty list when the sheet cannot be read.
        """
        url = f"https://docs.google.com/spreadsheets/d/{self.config.spreadsheet_id}/gviz/tq?tqx=out:csv"
        if self.config.worksheet:
            # A worksheet name is free text; `&`, `#` or `+` in it would otherwise cut or alter the query.
            url += f"&sheet={quote(self.config.worksheet, safe='')}"

        try:
            lines = self._fetch_csv(url).splitlines()
            return parse_rows(csv.DictReader(lines), self.config) if lines else []
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to fetch Google Sheet feedback: %s", e)
            return []
