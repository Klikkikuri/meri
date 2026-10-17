"""
Shared row parsing for the CSV and Google Sheets sources.

The two sources read the same export of the feedback form. This module maps the column headers to :class:`Feedback`
fields and parses each value. Of the page URL, it keeps only the parts that identify the article.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, tzinfo
from functools import cache, partial
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from ..abc import Feedback, FeedbackType
from ..settings.source import FeedbackSourceConfig

logger = logging.getLogger(__name__)

_HEADER_TO_FIELD = {
    "timestamp": "submitted_at",
    "pageurl": "page_url",
    "urlsign": "url_sign",
    "originaltitle": "original_title",
    "convertedtitle": "converted_title",
    "feedbacktype": "type",
    "clickbaitlevel": "clickbait_level",
    "comment": "message",
    "databaseupdated": "database_updated",
}

_NO_DATE = frozenset({"-", "unknown"})
"""Lowercase placeholders that the extension sends when it has no dataset date."""


def _normalize_header(header: str) -> str:
    """Reduce a spreadsheet header to letters and digits, so that case, spaces and quotes have no effect."""
    return re.sub(r"[^a-z0-9]", "", header.lower())


def _map_headers(row: Mapping[str, str | None]) -> dict[str, str]:
    """Return the non-blank cells of the known columns, keyed by :class:`Feedback` field name."""
    fields: dict[str, str] = {}
    for header, value in row.items():
        if header is None or value is None or not value.strip():
            continue
        if field := _HEADER_TO_FIELD.get(_normalize_header(header)):
            fields[field] = value
    return fields


def _optional[T](parse: Callable[[str], T], raw: str | None) -> T | None:
    """Apply ``parse`` to a cell. Return None when the cell is missing."""
    return parse(raw) if raw else None


def _parse_type(raw: str) -> FeedbackType | None:
    """Map a ``feedbackType`` cell to a :class:`FeedbackType`, and ignore stray punctuation."""
    for candidate in (raw.strip(), re.sub(r"[^a-zA-Z0-9_]", "", raw)):
        try:
            return FeedbackType(candidate)
        except ValueError:
            continue
    logger.warning("Unknown feedbackType: %r", raw)
    return None


def _parse_submitted_at(raw: str, config: FeedbackSourceConfig) -> datetime | None:
    """
    Read the form timestamp and return it in UTC.

    The column has no timezone, and the spreadsheet locale sets its format. The source configuration gives both.
    """
    try:
        naive = datetime.strptime(raw.strip(), config.timestamp_format)  # noqa: DTZ007 — tz comes from config below
        return naive.replace(tzinfo=_timezone(config.timezone)).astimezone(UTC)
    except (ValueError, OverflowError):
        logger.warning("Unparseable feedback timestamp %r for format %r", raw, config.timestamp_format)
        return None


@cache
def _timezone(name: str) -> tzinfo:
    """Return the timezone with this name. For an unknown name, log one warning and return UTC."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown feedback source timezone %r, reading timestamps as UTC", name)
        return UTC


def _parse_page_url(raw: str) -> str | None:
    """
    Return the page URL without its query, fragment and user credentials.

    These parts can identify the reader, so remove them from all URLs, also from relative URLs. The URL signature
    identifies the article. Return None when the URL does not parse, because then the parts cannot be removed safely.
    """
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    return parts._replace(netloc=parts.netloc.rpartition("@")[2], query="", fragment="").geturl()


def _parse_database_updated(raw: str) -> datetime | None:
    """Read the generation time of the dataset that the reader's extension used, and return it in UTC."""
    value = raw.strip()
    if value.lower() in _NO_DATE:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        logger.warning("Unparseable dataset generation time in a feedback row")
        return None


def _parse_row(row: Mapping[str, str | None], config: FeedbackSourceConfig) -> Feedback | None:
    """
    Parse one spreadsheet row into a :class:`Feedback` item.

    Return None when the row has no URL signature or no known feedback type, because Luotsi cannot match it to an
    article. Return None also when the row does not validate. A value that does not parse becomes None, and the row
    stays.
    """
    cells = _map_headers(row)
    url_sign = cells.get("url_sign")
    if not url_sign:
        return None
    feedback_type = _optional(_parse_type, cells.get("type"))
    if feedback_type is None:
        return None

    parse_time = partial(_parse_submitted_at, config=config)
    try:
        return Feedback(
            type=feedback_type,
            message=cells.get("message", ""),
            url_sign=url_sign,
            submitted_at=_optional(parse_time, cells.get("submitted_at")),
            page_url=_optional(_parse_page_url, cells.get("page_url")),
            original_title=cells.get("original_title"),
            converted_title=cells.get("converted_title"),
            clickbait_level=cells.get("clickbait_level"),
            database_updated=_optional(_parse_database_updated, cells.get("database_updated")),
        )
    except ValidationError as e:
        logger.warning("Failed to validate feedback row: %s", e)
        return None


def parse_rows(rows: Iterable[Mapping[str, str | None]], config: FeedbackSourceConfig) -> list[Feedback]:
    """
    Convert raw spreadsheet rows into validated :class:`Feedback` items.

    :param rows: Row mappings, as :class:`csv.DictReader` makes them.
    :param config: The source configuration. It gives the timestamp format and timezone.
    :return: Validated feedback items. Rows that :func:`_parse_row` rejects are not included.
    """
    return [feedback for row in rows if (feedback := _parse_row(row, config))]
