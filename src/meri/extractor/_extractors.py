from copy import deepcopy
from datetime import datetime, timezone
from random import randint
from typing import Generator, Iterable, Optional

import fastfeedparser
from opentelemetry import trace
from pydantic import AnyHttpUrl, HttpUrl
from structlog import get_logger

from meri.abc import ArticleMeta, LinkLabel, article_url
from meri.article import Article
from meri.scraper import get_user_agent
from meri.utils import clean_url, detect_language

from ._common import HtmlArticle, merge_article_lists

tracer = trace.get_tracer(__name__)

logger = get_logger(__name__)


class TrafilaturaArticle(HtmlArticle):
    """
    An article extracted using the trafilatura library.
    """

    pass


@tracer.start_as_current_span(name="trafilatura_extractor", kind=trace.SpanKind.CLIENT)
def trafilatura_extractor(url: AnyHttpUrl | str) -> TrafilaturaArticle:
    """
    Extract the article using the trafilatura library.
    """
    from trafilatura import bare_extraction, fetch_url
    from trafilatura.settings import DEFAULT_CONFIG

    # Find date in ISO 8601 format
    date_iso_format = r"%Y-%m-%dT%H:%M:%S%z"

    # https://trafilatura.readthedocs.io/en/latest/settings.html
    config = deepcopy(DEFAULT_CONFIG)
    config["DEFAULT"]["USER_AGENTS"] = get_user_agent()
    config["DEFAULT"].setdefault("DOWNLOAD_TIMEOUT", str(randint(5, 12)))  # nosec
    config["DEFAULT"].setdefault("SLEEP_TIME", str(randint(1, 5)))  # nosec

    url = clean_url(str(url))

    downloaded = fetch_url(url, config=config)

    document = bare_extraction(
        downloaded,
        url=url,
        include_formatting=True,
        include_comments=False,
        include_images=True,
        with_metadata=True,
        include_tables=True,
        include_links=True,
        config=config,
        date_extraction_params={"outputformat": date_iso_format, "deferred_url_extractor": True, "max_date": None },
    )

    # # TODO: find_date is bad at finding "created" dates, it often finds "modified" dates instead
    # date_published = find_date(downloaded, url=url, outputformat=iso_format, original_date=True, deferred_url_extractor=True)
    # date_modified = find_date(downloaded, url=url, outputformat=iso_format, original_date=False, deferred_url_extractor=True)

    article = TrafilaturaArticle(
        meta=ArticleMeta(
            title=document.title,
            language=document.language or detect_language(document.text),
            authors=[] if not document.author else [document.author],
            date=document.date,
        ),
        text=document.text,
        urls=[
            article_url(document.url, labels=[LinkLabel.LINK_CANONICAL]),
        ],
        created_at=None,
        updated_at=None,

        html=downloaded
    )


    # Check if the date is tz-aware.
    if document and document.date:
        parsed_date = datetime.fromisoformat(document.date)
        article.updated_at = parsed_date

        # TODO: Implement timezone mapping if not present in the extracted date.
        if parsed_date.tzinfo is None:
            logger.warning("Extracted date is not timezone-aware. Implement timezone mapping if needed.", extracted_date=document.date)

    else:
        article.created_at = datetime.now(timezone.utc)

    if document.url != url:
        article.urls.append(article_url(url, type=LinkLabel.LINK_MOVED))

    return article


class TrafilaturaExtractorMixin:
    """
    Use :class:`trafilatura.Extractor` to extract information from a news article.
    """

    # processors: list[Callable] = [
    #     check_robots_txt_access,
    #     trafilatura_extractor,
    # ]

    def fetch_by_url(self, url: AnyHttpUrl | str) -> TrafilaturaArticle:
        """
        Fetch the article from the URL using :class:`trafilatura.Extractor`.
        """
        url = clean_url(str(url))

        article = trafilatura_extractor(url)
        if not article or not article.text:
            raise ValueError(f"Failed to extract article from URL {url}")

        return article


class RssFeedMixin:
    """
    Mixin to add RSS feed parsing capabilities to an outlet.

    To use, subclass and set the :py:attr:`feed_urls` attribute to a list of RSS feed URLs.

    ..example::
        class MyOutlet(RssFeedMixin, Outlet):
            name = "My Outlet"
            valid_url = r"://myoutlet.com/"
            feed_urls = [
                "https://myoutlet.com/rss",
            ]

    Uses :py:mod:`fastfeedparser` to parse the feed and extract articles.
    """

    feed_urls: list[AnyHttpUrl | str] = []

    def latest(self) -> list[Article]:
        """
        Fetch the latest articles from the RSS feed.
        """

        articles: list[Article] = []

        if len(self.feed_urls) == 0:
            logger.warning("No RSS feed URL configured for outlet %r, skipping.", self.name)
            return articles

        urls = set(clean_url(str(u)) for u in self.feed_urls)

        rss_feeds = [RssParser(url) for url in urls]
        articles = merge_article_lists(*rss_feeds)

        logger.debug("Fetched %d articles from RSS feeds", len(articles), extra={
            "feed_urls": urls,
            "updated": [f.feed['feed']['updated'] for f in rss_feeds],
        })
        return articles


class RssParser(Iterable[Article]):
    """
    Convert RSS feed data to a list of articles. Supports iteration over articles.

    TODO: Use own network fetching code.
    """
    feed: fastfeedparser.FastFeedParserDict
    url: HttpUrl
    language: Optional[str]

    def __init__(self, url: str | HttpUrl, language: str | None = None):
        self.url = HttpUrl(url)
        self.language = language

    def __iter__(self) -> Generator[Article, None, None]:
        """Make the parser iterable using yield."""
        self.feed = fastfeedparser.parse(str(self.url))

        for entry in self.feed.entries:

            content = entry.get("description", None).strip()
            content_lang = self.language

            # Check if "actual" - or more likely to terse - content is available
            if 'content' in entry and entry.content:
                if len(entry.content) > 1:
                    logger.warning("Multiple content entries found, using the first one.", extra={"entry": entry, "url": self.url, "feed": self.feed.feed})

                for i, content_entry in enumerate(entry.content):
                    if 'value' not in content_entry or not content_entry['value'].strip():
                        # Skip entries without 'value' key
                        logger.debug("Skipping content entry without 'value' key or empty value.", extra={"entry": entry, "index": i, "url": self.url, "feed": self.feed.feed})
                        continue

                    _content = content_entry['value'].strip()

                    # TODO: Maybe implement language filtering here?
                    content_lang = content_entry.get("language", content_lang)

                    mime_type = content_entry.get("type", "").strip()
                    match mime_type:
                        case "text/plain":
                            _content = content_entry['value'].strip()
                        case "text/html":
                            # TODO: Fix import cycle
                            from ._processors import html_to_markdown

                            _content = html_to_markdown(_content)
                        case _:
                            logger.warning("Unknown content type, skipping.", extra={"entry": entry, "index": i, "url": self.url, "feed": self.feed.feed})
                            continue
                    break

            url = entry.get("link", "").strip()
            if not url:
                logger.warning("Entry %r missing url (`link` -field), skipping.", entry['title'], extra={"entry": entry, "url": self.url, "feed": self.feed.feed})
                continue

            published = None
            if _published := entry.get("published", None):
                published = datetime.fromisoformat(_published)
            else:
                published = datetime.now(timezone.utc)

            article = Article(
                text=content,
                meta=ArticleMeta({
                    "title": entry.get("title", "").strip(),
                    "language": content_lang,
                    "authors": [author.strip() for author in entry.get("author", "").split(",") if author.strip()],
                    "id": entry.get("id", entry.get("link", "")).strip(),
                }),
                created_at=published,
                urls=[article_url(url)]
            )

            yield article

    def parse(self) -> list[Article]:
        """Parse and return all articles as a list."""
        return list(self)
