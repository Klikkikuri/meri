"""
Video content detection using structured data in HTML.

Supports JSON-LD, Microdata, and RDFa formats.

See:
 - <https://developers.google.com/search/docs/appearance/structured-data/video>
 - <https://schema.org/VideoObject>
"""

import json
from typing import Any

from lxml import html
from niitti import get_logger

logger = get_logger(__name__)


def is_video_content(html_string: str) -> bool:
    """
    Detect if an HTML page contains video content using structured data.

    This performs a loose match, returning True if a VideoObject is found
    anywhere in the JSON-LD graph or DOM. The pipeline determines if the page
    is primarily a video based on text length.

    Checks, in order: JSON-LD -> Microdata -> RDFa.

    :param html_string: Raw HTML of the page.
    :return: True if a VideoObject structured-data indicator is found.
    """
    try:
        tree = html.fromstring(html_string)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to parse HTML for video detection: %s", e)
        return False

    return (
        _check_jsonld_video(tree)
        or _check_microdata_video(tree)
        or _check_rdfa_video(tree)
    )


def _check_jsonld_video(tree: Any) -> bool:
    """Return True if any JSON-LD block declares a VideoObject."""
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        if script.text is None:
            continue
        try:
            data = json.loads(script.text)
            if _has_video_object(data):
                return True
        except (json.JSONDecodeError, TypeError):
            logger.debug("Invalid JSON in JSON-LD script, skipping")
    return False


def _has_video_object(data: dict | list) -> bool:
    """Recursively walk JSON-LD data to find a VideoObject."""
    if isinstance(data, list):
        return any(_has_video_object(item) for item in data if isinstance(item, (dict, list)))
    if not isinstance(data, dict):
        return False

    schema_type = data.get("@type", "")
    types: list[str] = []
    if isinstance(schema_type, list):
        types = [t for t in schema_type if isinstance(t, str)]
    elif isinstance(schema_type, str) and schema_type:
        types = [schema_type]

    if any(t == "VideoObject" or t.endswith("/VideoObject") for t in types):
        return True

    # Recursively check all nested objects and lists
    for value in data.values():
        if isinstance(value, (dict, list)) and _has_video_object(value):
            return True

    return False


def _check_microdata_video(tree: Any) -> bool:
    """Return True if any element declares schema.org/VideoObject as its itemtype."""
    # Match both http and https schema.org URIs anywhere in the DOM
    xpath = '//*[contains(@itemtype, "schema.org/VideoObject")]'
    try:
        return bool(tree.xpath(xpath))
    except Exception:  # nosec B110 # noqa: BLE001
        return False


def _check_rdfa_video(tree: Any) -> bool:
    """Return True if any element uses schema.org/VideoObject as a typeof."""
    xpath = (
        '//*[contains(@typeof, "schema.org/VideoObject") or @typeof="VideoObject" '
        'or contains(@type, "schema.org/VideoObject") or @type="VideoObject"]'
    )
    try:
        return bool(tree.xpath(xpath))
    except Exception:  # nosec B110 # noqa: BLE001
        return False
