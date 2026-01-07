"""
RSS/Atom feed discoverer for extracting article URLs from feeds.
"""

from datetime import datetime, timezone
from typing import Generator, Iterable, Optional

import fastfeedparser
from pydantic import HttpUrl
from structlog import get_logger

from meri.abc import ArticleMeta, article_url
from meri.article import Article

from ._base import SourceDiscoverer
from ._registry import registry

logger = get_logger(__name__)


@registry.register("rss")
class RSSDiscoverer(SourceDiscoverer):
    """
    Discover article URLs from RSS/Atom feeds.
    
    This discoverer parses RSS/Atom feeds and extracts article URLs along with
    basic metadata like title, published date, and description.
    """
    
    def discover(self, source_url: HttpUrl, **kwargs) -> list[Article]:
        """
        Fetch articles with metadata from an RSS/Atom feed.
        
        Returns full Article objects with metadata extracted from the feed
        (title, description, dates, etc.).
        
        :param source_url: URL of the RSS/Atom feed
        :param kwargs: Optional parameters including 'language' for default language
        :return: List of Article objects with metadata
        """
        language = kwargs.get('language')
        parser = RssParser(str(source_url), language=language)
        articles = parser.parse()
        
        return articles


class RssParser(Iterable[Article]):
    """
    Convert RSS feed data to a list of articles. Supports iteration over articles.
    
    This parser uses fastfeedparser to parse RSS/Atom feeds and converts entries
    to Article objects with metadata.
    """
    feed: fastfeedparser.FastFeedParserDict
    url: HttpUrl
    language: Optional[str]

    def __init__(self, url: str | HttpUrl, language: str | None = None):
        """
        Initialize RSS parser.
        
        :param url: URL of the RSS/Atom feed
        :param language: Default language for articles if not specified in feed
        """
        self.url = HttpUrl(url)
        self.language = language

    def __iter__(self) -> Generator[Article, None, None]:
        """Make the parser iterable using yield."""
        self.feed = fastfeedparser.parse(str(self.url))

        for entry in self.feed.entries:

            content = entry.get("description", "").strip()
            content_lang = self.language

            # Check if "actual" - or more likely less terse - content is available
            if 'content' in entry and entry.content:
                if len(entry.content) > 1:
                    logger.warning(
                        "Multiple content entries found, using the first one.",
                        entry_title=entry.get("title", ""),
                        url=str(self.url)
                    )

                for i, content_entry in enumerate(entry.content):
                    if 'value' not in content_entry or not content_entry['value'].strip():
                        # Skip entries without 'value' key
                        logger.debug(
                            "Skipping content entry without 'value' key or empty value.",
                            entry_title=entry.get("title", ""),
                            index=i
                        )
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
                            from meri.extractor._processors import html_to_markdown

                            _content = html_to_markdown(_content)
                        case _:
                            logger.warning(
                                "Unknown content type, skipping.",
                                mime_type=mime_type,
                                entry_title=entry.get("title", "")
                            )
                            continue
                    
                    content = _content
                    break

            url = entry.get("link", "").strip()
            if not url:
                logger.warning(
                    "Entry missing url (`link` field), skipping.",
                    entry_title=entry.get("title", ""),
                    feed_url=str(self.url)
                )
                continue

            published = None
            if _published := entry.get("published", None):
                try:
                    published = datetime.fromisoformat(_published)
                except (ValueError, TypeError):
                    logger.warning(
                        "Failed to parse published date",
                        published=_published,
                        entry_title=entry.get("title", "")
                    )
                    published = datetime.now(timezone.utc)
            else:
                published = datetime.now(timezone.utc)

            article = Article(
                text=content,
                meta=ArticleMeta({
                    "title": entry.get("title", "").strip(),
                    "language": content_lang,
                    "authors": [
                        author.strip() 
                        for author in entry.get("author", "").split(",") 
                        if author.strip()
                    ],
                    "id": entry.get("id", entry.get("link", "")).strip(),
                }),
                created_at=published,
                updated_at=None,
                urls=[article_url(url)]
            )

            yield article

    def parse(self) -> list[Article]:
        """Parse and return all articles as a list."""
        return list(self)
