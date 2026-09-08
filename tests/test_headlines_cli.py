"""Tests for `meri headlines` — the discovery-only listing used to see what a run would pick up."""

from datetime import UTC, datetime, timedelta

from click.testing import CliRunner

from meri.abc import article_url
from meri.article import Article
from meri.cli.headlines import cli, select
from meri.lautta import DiscoveredArticle
from meri.settings.newssources import NewsSource
from meri.settings.settings import Settings

SOURCE = NewsSource.model_construct(name="Test Source", type="rss", url="https://example.com")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def discovered(slug: str, age_hours: int | None) -> DiscoveredArticle:
    created = None if age_hours is None else NOW - timedelta(hours=age_hours)
    article = Article(urls=[article_url(f"https://example.com/{slug}")], meta={"title": slug}, created_at=created)
    return DiscoveredArticle(source=SOURCE, article=article)


ARTICLES = [discovered("old", 48), discovered("undated", None), discovered("new", 1), discovered("mid", 24)]


def test_select_orders_newest_first_and_undated_last():
    assert [d.article.title for d in select(ARTICLES, None, sample=False)] == ["new", "mid", "old", "undated"]


def test_select_limit_keeps_the_newest():
    assert [d.article.title for d in select(ARTICLES, 2, sample=False)] == ["new", "mid"]


def test_select_sample_draws_limit_articles_without_repeats():
    picked = select(ARTICLES, 3, sample=True)
    assert len(picked) == 3
    assert len({d.article.title for d in picked}) == 3
    assert len(select(ARTICLES, 10, sample=True)) == 4


def test_command_prints_one_line_per_headline(monkeypatch):
    monkeypatch.setattr("meri.cli.headlines.fetch_latest", lambda _sources: ARTICLES)
    settings = Settings.model_validate({"rahti": {"url": "file:///app/instance/rahti/data.json"}})

    result = CliRunner().invoke(cli, ["--limit", "2"], obj={"settings": settings})

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "2026-09-08T11:00+00:00\tTest Source\tnew\thttps://example.com/new",
        "2026-09-07T12:00+00:00\tTest Source\tmid\thttps://example.com/mid",
    ]
