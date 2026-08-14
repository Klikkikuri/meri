from unittest.mock import patch, MagicMock
import pytest

from meri.abc import article_url
from meri.article import Article
from meri.lautta import (
    DiscoveredArticle,
    fetch_full_articles,
    fetch_latest,
    generate_titles,
)
from meri.settings.newssources import NewsSource


def make_discovered_article(url_str: str) -> DiscoveredArticle:
    source = NewsSource.model_construct(name="Test Source", enabled=True, type="rss", url="https://example.com")
    article = Article(
        urls=[article_url(url_str)],
        labels=[],
        text="Sample article body text with sufficient word count to pass minimum text length requirements for processing.",
    )
    return DiscoveredArticle(source=source, article=article)


def test_generate_titles_single_failure_continues():
    art1 = make_discovered_article("https://example.com/art1")
    art2 = make_discovered_article("https://example.com/art2")

    mock_response = MagicMock(title="Success Title")

    def mock_predictor_run(self, article, **kwargs):
        if "art1" in str(article.get_url()):
            raise RuntimeError("LLM API failure for art1")
        return mock_response

    with patch("meri.pipelines.title.TitlePredictor.run", mock_predictor_run):
        results = generate_titles([art1, art2])

    assert len(results) == 2
    assert results[0].title is None
    assert results[1].title is mock_response


def test_generate_titles_all_failed_raises():
    art1 = make_discovered_article("https://example.com/art1")

    def mock_predictor_run(self, article, **kwargs):
        raise RuntimeError("LLM API failure for all")

    with patch("meri.pipelines.title.TitlePredictor.run", mock_predictor_run):
        with pytest.raises(RuntimeError, match="All article title generations failed"):
            generate_titles([art1])


def test_fetch_latest_all_failed_raises():
    source = NewsSource.model_construct(name="Failing Source", enabled=True, type="rss", url="https://example.com")

    def mock_discover(src):
        raise RuntimeError("Network error")

    with patch("meri.lautta.discover_articles", mock_discover):
        with pytest.raises(RuntimeError, match="All enabled news sources failed to fetch articles"):
            fetch_latest([source])


def test_fetch_full_articles_all_failed_raises():
    art1 = make_discovered_article("https://example.com/art1")

    def mock_extractor(url):
        raise RuntimeError("Extraction failed")

    with patch("meri.lautta.get_extractor", mock_extractor):
        with pytest.raises(RuntimeError, match="All full article fetches failed"):
            fetch_full_articles([art1])
