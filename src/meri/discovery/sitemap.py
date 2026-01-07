"""
Sitemap page discoverer for extracting article URLs from XML sitemaps.
"""

from datetime import datetime, timezone
from typing import Generator, Iterable, Optional

from pydantic import HttpUrl
from structlog import get_logger
from usp.tree import sitemap_tree_for_homepage
from usp.web_client.requests_client import RequestsWebClient
import requests

from meri.abc import ArticleMeta, article_url
from meri.article import Article
from meri.settings import settings

from ._base import SourceDiscoverer
from ._registry import registry

logger = get_logger(__name__)


def _create_web_client(timeout: int = 10) -> RequestsWebClient:
    """
    Create a RequestsWebClient configured with the application's user agent.
    
    :param timeout: Request timeout behavior (seconds to wait between requests)
    :return: Configured RequestsWebClient instance
    """
    # Create a custom session with the user agent and timeout
    session = requests.Session()
    session.headers.update({
        'User-Agent': settings.BOT_USER_AGENT
    })
    # Set timeout on the session's request method
    original_request = session.request
    def request_with_timeout(*args, **kwargs):
        if 'timeout' not in kwargs:
            kwargs['timeout'] = timeout
        return original_request(*args, **kwargs)
    session.request = request_with_timeout
    
    # Create the web client with the custom session
    client = RequestsWebClient(session=session)
    return client


@registry.register("sitemap")
class SitemapDiscoverer(SourceDiscoverer):
    """
    Discover article URLs from XML sitemaps.
    
    This discoverer uses the Ultimate Sitemap Parser library to parse XML sitemaps
    and extract article URLs along with basic metadata like last modification date
    and priority.
    """
    
    def discover(self, source_url: HttpUrl, **kwargs) -> list[Article]:
        """
        Fetch articles with metadata from a website's sitemaps.
        
        Returns Article objects extracted from the sitemap(s) found at the source URL.
        By default, does not traverse nested sitemap indexes (max_depth=1).
        
        :param source_url: URL of the website or direct sitemap URL
        :param kwargs: Optional parameters:
            - 'language': Default language for articles
            - 'timeout': Request timeout in seconds (default: 10)
            - 'max_depth': Maximum depth to traverse (default: 1, no traversal)
        :return: List of Article objects with metadata
        """
        language = kwargs.get('language')
        timeout = kwargs.get('timeout', 10)
        max_depth = kwargs.get('max_depth', 1)  # Default: don't traverse nested sitemaps
        parser = SitemapParser(str(source_url), language=language, timeout=timeout, max_depth=max_depth)
        articles = parser.parse()
        
        return articles


class SitemapParser(Iterable[Article]):
    """
    Convert sitemap data to a list of articles. Supports iteration over articles.
    
    This parser uses ultimate-sitemap-parser to discover and parse XML sitemaps
    and converts pages to Article objects with metadata.
    """
    url: HttpUrl
    language: Optional[str]

    def __init__(self, url: str | HttpUrl, language: str | None = None, timeout: int = 10, max_depth: int = 1):
        """
        Initialize sitemap parser.
        
        :param url: URL of the website or direct sitemap URL
        :param language: Default language for articles if not specified in sitemap
        :param timeout: Request timeout in seconds (default: 10s)
        :param max_depth: Maximum depth to traverse in sitemap index (default: 1 = no traversal)
        """
        self.url = HttpUrl(url)
        self.language = language
        self.timeout = timeout
        self.max_depth = max_depth

    def __iter__(self) -> Generator[Article, None, None]:
        """Make the parser iterable using yield."""
        try:
            # Create web client with proper user agent and timeout
            web_client = _create_web_client(timeout=self.timeout)
            
            # Define callback to prevent traversal of nested sitemaps
            def prevent_traversal(url: str, depth: int, processed: set) -> bool:
                """Prevent traversal beyond max_depth."""
                return depth < self.max_depth
            
            tree = sitemap_tree_for_homepage(
                str(self.url),
                web_client=web_client,
                recurse_callback=prevent_traversal  # Prevent traversal of nested sitemaps
            )
        except Exception as e:
            logger.error(
                "Failed to fetch sitemap tree",
                url=str(self.url),
                timeout=self.timeout,
                max_depth=self.max_depth,
                error=str(e)
            )
            return

        for page in tree.all_pages():
            url = page.url
            if not url:
                logger.debug(
                    "Skipping page without URL",
                    source_url=str(self.url)
                )
                continue

            try:
                # Extract metadata from the sitemap page
                last_modified = None
                if hasattr(page, 'last_modified') and page.last_modified:
                    try:
                        if isinstance(page.last_modified, datetime):
                            last_modified = page.last_modified
                        else:
                            # Try to parse as ISO format string
                            last_modified = datetime.fromisoformat(str(page.last_modified))
                    except (ValueError, TypeError) as e:
                        logger.debug(
                            "Failed to parse last_modified date",
                            last_modified=page.last_modified,
                            url=url,
                            error=str(e)
                        )
                
                if not last_modified:
                    last_modified = datetime.now(timezone.utc)

                # Get priority if available
                priority = None
                if hasattr(page, 'priority') and page.priority is not None:
                    try:
                        priority = float(page.priority)
                    except (ValueError, TypeError):
                        priority = None

                # Extract change frequency if available
                change_freq = None
                if page.change_frequency is not None:
                    change_freq = str(page.change_frequency).strip()

                # Build metadata dict with Google News extensions if available
                meta_dict: ArticleMeta = {
                    "language": self.language,
                }
                
                # Extract Google News extensions if present
                news_story = None
                if hasattr(page, 'news_story') and page.news_story:
                    news_story = page.news_story
                    
                    # Use Google News title as article title
                    if news_story.title:
                        meta_dict["title"] = news_story.title
                    
                    # Use publication name as outlet
                    if news_story.publication_name:
                        meta_dict["outlet"] = news_story.publication_name
                    
                    # Override language if Google News specifies it
                    if news_story.publication_language:
                        meta_dict["language"] = news_story.publication_language
                    
                    # Use publish date as created_at
                    if news_story.publish_date:
                        last_modified = news_story.publish_date
                
                # Add optional metadata if available
                if priority is not None:
                    meta_dict["id"] = f"{url}#{priority}"
                
                article = Article(
                    text="",  # Sitemaps don't contain article text
                    meta=ArticleMeta(meta_dict),
                    created_at=last_modified,
                    updated_at=None,
                    urls=[article_url(url)]
                )
                
                # Log with change frequency and news extensions if available
                log_data = {
                    "url": url,
                    "priority": priority,
                    "last_modified": last_modified,
                }
                if change_freq:
                    log_data["change_frequency"] = change_freq
                if news_story:
                    log_data["title"] = news_story.title
                    log_data["publication"] = news_story.publication_name
                    log_data["language"] = news_story.publication_language
                    if news_story.keywords:
                        log_data["keywords"] = ", ".join(news_story.keywords[:3])
                
                logger.debug("Found article in sitemap", **log_data)

                yield article

            except Exception as e:
                logger.warning(
                    "Failed to process sitemap page",
                    url=url,
                    error=str(e)
                )
                continue

    def parse(self) -> list[Article]:
        """Parse and return all articles as a list."""
        return list(self)
