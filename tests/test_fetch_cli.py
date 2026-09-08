"""Tests for `meri fetch` — the extraction-only dump of one article as Markdown."""

from datetime import UTC, datetime

from click.testing import CliRunner

from meri.abc import ArticleLabels, article_url
from meri.article import Article
from meri.cli.fetch import cli, render

ARTICLE = Article(
    urls=[article_url("https://example.com/story")],
    meta={"title": "A story", "outlet": "Example", "authors": ["Ann", "Bo"], "language": "fi"},
    labels=[ArticleLabels.PAYWALLED],
    text="First paragraph.\n\n## Subheading\n\nSecond paragraph.",
    created_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
)


def test_render_lays_out_title_metadata_and_text():
    assert render(ARTICLE).splitlines() == [
        "# A story",
        "",
        "- **URL:** https://example.com/story",
        "- **Outlet:** Example",
        "- **Authors:** Ann, Bo",
        "- **Language:** fi",
        "- **Created:** 2026-09-08 12:00:00+00:00",
        "- **Labels:** com.github.klikkikuri/paywalled=true",
        "",
        "First paragraph.",
        "",
        "## Subheading",
        "",
        "Second paragraph.",
    ]


def test_render_skips_missing_metadata_and_marks_missing_text():
    bare = Article(urls=[article_url("https://example.com/bare")])
    assert render(bare).splitlines() == [
        "# (untitled)",
        "",
        "- **URL:** https://example.com/bare",
        "",
        "*(no text extracted)*",
    ]


def test_command_fetches_through_the_matched_extractor(monkeypatch):
    seen = {}

    class FakeExtractor:
        def fetch_by_url(self, url):
            seen["url"] = url
            return ARTICLE

    monkeypatch.setattr("meri.cli.fetch.get_extractor", lambda url: FakeExtractor())

    result = CliRunner().invoke(cli, ["https://example.com/story"])

    assert result.exit_code == 0, result.output
    assert seen["url"] == "https://example.com/story"
    assert result.output == render(ARTICLE)
