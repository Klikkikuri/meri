"""
Sitemap page discoverer for extracting article URLs from XML sitemaps.
"""

from datetime import datetime, timezone

from pydantic import HttpUrl
from structlog import get_logger
from usp.tree import sitemap_from_str
import requests

from meri.abc import ArticleMeta, article_url
from meri.article import Article
from meri.settings import settings

from ._base import SourceDiscoverer
from ._registry import registry

logger = get_logger(__name__)


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
        session = requests.Session()
        session.headers.update({
            'User-Agent': settings.BOT_USER_AGENT,
            'Accept-Encoding': 'gzip, deflate',
        })
        res = session.get(str(source_url), timeout=kwargs.get('timeout', 10))
        res.raise_for_status()
        sitemap_content = res.text
        tree = sitemap_from_str(sitemap_content)
        articles = []

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
                meta_dict: ArticleMeta = {}

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

                articles.append(article)

            except Exception as e:
                logger.warning(
                    "Failed to process sitemap page",
                    url=url,
                    error=str(e)
                )
                continue

        return articles
