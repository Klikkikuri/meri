"""
Shared parsing helper for CSV and Google Sheet tabular data.
"""

import csv
import logging
import re
from ..abc import Feedback, FeedbackType

logger = logging.getLogger(__name__)

def parse_csv_rows(reader: csv.DictReader, signature: str | None = None) -> list[Feedback]:
    """
    Parse rows from a csv.DictReader and convert them to validated Feedback models.

    Standardises variant header names (ignoring casing, spacing, and quotes)
    and verifies required fields.

    Args:
        reader: A DictReader containing row dictionaries.
        signature: Optional signature filter to only include matching articles.

    Returns:
        A list of validated Feedback items.
    """
    feedbacks: list[Feedback] = []

    for row in reader:
        cleaned_row = {}
        for k, v in row.items():
            if k is None or v is None:
                continue
            # Clean the key to be letters/numbers only in lowercase
            clean_k = re.sub(r'[^a-z0-9]', '', k.lower())

            # Map to target field
            if clean_k == "timestamp":
                cleaned_row["timestamp"] = v
            elif clean_k == "pageurl":
                cleaned_row["page_url"] = v
            elif clean_k == "urlsign":
                cleaned_row["url_sign"] = v
            elif clean_k == "originaltitle":
                cleaned_row["original_title"] = v
            elif clean_k == "convertedtitle":
                cleaned_row["converted_title"] = v
            elif clean_k == "feedbacktype":
                val = v.strip()
                try:
                    cleaned_row["type"] = FeedbackType(val)
                except ValueError:
                    val_clean = re.sub(r'[^a-zA-Z0-9_]', '', val)
                    try:
                        cleaned_row["type"] = FeedbackType(val_clean)
                    except ValueError:
                        logger.warning("Unknown feedbackType: %r", v)
                        cleaned_row["type"] = None
            elif clean_k == "clickbaitlevel":
                cleaned_row["clickbait_level"] = v
            elif clean_k == "comment":
                cleaned_row["message"] = v
            elif clean_k == "databaseupdated":
                cleaned_row["database_updated"] = v

        # Skip if required fields are missing
        if not cleaned_row.get("url_sign"):
            continue
        if not cleaned_row.get("type"):
            continue
        if cleaned_row.get("message") is None:
            cleaned_row["message"] = ""

        # Filter by signature if specified
        if signature and cleaned_row["url_sign"] != signature:
            continue

        try:
            feedback = Feedback(**cleaned_row)
            feedbacks.append(feedback)
        except Exception as e:
            logger.warning("Failed to validate CSV row: %s. Error: %s", cleaned_row, e)

    return feedbacks
