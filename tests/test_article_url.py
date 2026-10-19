"""
Tests for comparing article URLs by address and by signature.
"""

from unittest.mock import patch

import pytest
from pydantic import AnyHttpUrl

from meri.abc import ArticleUrl

FIRST = "https://example.com/a/1"
SECOND = "https://example.com/a/2"


def url(href: str) -> ArticleUrl:
    return ArticleUrl(href=AnyHttpUrl(href))


@pytest.mark.parametrize(
    ("signs", "equal"),
    [
        ({FIRST: "sig", SECOND: "sig"}, True),
        ({FIRST: "sig-1", SECOND: "sig-2"}, False),
        ({FIRST: None, SECOND: None}, False),
    ],
    ids=["same signature", "different signatures", "no signatures"],
)
def test_different_hrefs_compare_by_signature(signs, equal):
    with patch("meri.abc.hash_url", side_effect=lambda href: signs[str(href)]):
        assert (url(FIRST) == url(SECOND)) is equal


def test_same_href_is_equal_without_signature():
    with patch("meri.abc.hash_url", return_value=None):
        assert url(FIRST) == url(FIRST)
