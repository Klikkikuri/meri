"""
Shared row parsing for the CSV and Google Sheets sources.

Both sources receive the same tabular export of the feedback form, so header normalization and field mapping live
here.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..abc import Feedback, FeedbackType
from ..settings.source import FeedbackSourceConfig

logger = logging.getLogger(__name__)

_HEADER_TO_FIELD = {
    "timestamp": "timestamp",
    "pageurl": "page_url",
    "urlsign": "url_sign",
    "originaltitle": "original_title",
    "convertedtitle": "converted_title",
    "feedbacktype": "type",
    "clickbaitlevel": "clickbait_level",
    "comment": "message",
    "databaseupdated": "database_updated",
}


def _normalize_header(header: str) -> str:
    """Reduce a spreadsheet header to letters and digits, so casing, spacing and quoting do not matter."""
    return re.sub(r"[^a-z0-9]", "", header.lower())


def _parse_type(raw: str) -> FeedbackType | None:
    """Map a ``feedbackType`` cell to a :class:`FeedbackType`, tolerating stray punctuation."""
    for candidate in (raw.strip(), re.sub(r"[^a-zA-Z0-9_]", "", raw)):
        try:
            return FeedbackType(candidate)
        except ValueError:
            continue
    logger.warning("Unknown feedbackType: %r", raw)
    return None


def _parse_submitted_at(raw: str, config: FeedbackSourceConfig) -> datetime | None:
    """
    Read the form timestamp with the source's configured format and timezone, and return it in UTC.

    The column carries no timezone and its layout follows the spreadsheet locale, so both come from configuration.
    """
    try:
        naive = datetime.strptime(raw.strip(), config.timestamp_format)  # noqa: DTZ007 — tz comes from config below
    except ValueError:
        logger.warning("Unparseable feedback timestamp %r for format %r", raw, config.timestamp_format)
        return None

    try:
        tz = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown feedback source timezone %r, reading timestamps as UTC", config.timezone)
        tz = UTC

    return naive.replace(tzinfo=tz).astimezone(UTC)


def parse_rows(rows: Iterable[Mapping[str, str | None]], config: FeedbackSourceConfig) -> list[Feedback]:
    """
    Convert raw spreadsheet rows into validated :class:`Feedback` items.

    Rows without a URL signature or with an unknown feedback type are dropped — they cannot be matched to an
    article or acted on.

    :param rows: Row mappings as produced by :class:`csv.DictReader`.
    :param config: The source configuration, for timestamp format and timezone.
    :return: Validated feedback items.
    """
    feedbacks: list[Feedback] = []

    for row in rows:
        fields: dict[str, object] = {}
        for header, value in row.items():
            if header is None or value is None:
                continue
            field = _HEADER_TO_FIELD.get(_normalize_header(header))
            if field:
                fields[field] = value

        if not fields.get("url_sign"):
            continue

        raw_type = fields.pop("type", None)
        feedback_type = _parse_type(str(raw_type)) if raw_type else None
        if feedback_type is None:
            continue
        fields["type"] = feedback_type

        raw_timestamp = fields.pop("timestamp", None)
        fields["submitted_at"] = _parse_submitted_at(str(raw_timestamp), config) if raw_timestamp else None

        fields.setdefault("message", "")

        try:
            feedbacks.append(Feedback(**fields))  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to validate feedback row: %s", e)

    return feedbacks
