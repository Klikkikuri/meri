"""Iltalehti API discoverer for fetching latest articles."""

from datetime import datetime, timedelta

import requests
from pydantic import HttpUrl
from pytz import utc
from structlog import get_logger

from meri.abc import ArticleMeta, article_url, LinkLabel
from meri.article import Article

from ._base import SourceDiscoverer
from ._registry import registry

logger = get_logger(__name__)

BASE_URL = "https://www.iltalehti.fi/{category[category_name]}/a/{article_id}"
LANGUAGE = "fi"

OUTLETS = {
    "iltalehti": "Iltalehti",
}

@registry.register("iltalehti")
class IltalehtiFeedDiscoverer(SourceDiscoverer):
    """
    Discover articles from Iltalehti's API endpoint.
    
    This discoverer fetches the latest articles from Iltalehti's JSON API,
    filtering out sponsored content and constructing Article objects with
    metadata.
    """
    
    def discover(self, source_url: HttpUrl | None = None, **kwargs) -> list[Article]:
        """Fetch latest articles from Iltalehti API.
        
        Args:
            source_url: Not used, kept for interface compatibility
            **kwargs: Optional parameters (not currently used)
            
        Returns:
            List of Article objects with metadata
        """

        response = requests.get(str(source_url), timeout=10)
        response.raise_for_status()
        data = response.json()

        articles = []
        for article_data in data["response"]:
            if article := self._parse_article(article_data):
                articles.append(article)
        
        return articles
    
    def _parse_article(self, article_data: dict) -> Article | None:
        """Parse a single article from API response.
        
        Args:
            article_data: Raw article data from API
            
        Returns:
            Article object or None if article should be skipped
        """
        # Skip sponsored content
        if article_data.get("metadata", {}).get("sponsored_content", False):
            logger.debug("Skipping sponsored content: %r", article_data["title"])
            return None
        
        # Validate required fields
        if not (published_at := article_data.get("published_at")):
            logger.warning("Article missing published_at, skipping: %r", article_data)
            return None
        
        # Build URLs
        urls = self._build_urls(article_data)
        
        # Parse timestamps
        created_at = datetime.fromisoformat(published_at).astimezone(utc)
        updated_at = self._parse_updated_at(article_data, created_at)
        
        # Validate timestamps
        now = datetime.now(utc)
        assert created_at <= now, "Creation date cannot be in the future"
        assert updated_at >= created_at, "Update date cannot be before creation date"
        assert updated_at > (now - timedelta(days=2)), "Article is too old"
       
        # Determine outlet - purposefully crashes if unknown
        outlet = OUTLETS[article_data.get("service_name", "").lower()]

        # Build metadata
        meta = ArticleMeta({
            "title": article_data["title"],
            "outlet": outlet,
            "language": LANGUAGE,
            "id": str(article_data["article_id"]),
        })
        
        return Article(
            text=article_data.get("lead", ""),
            urls=urls,
            meta=meta,
            created_at=created_at,
            updated_at=updated_at,
        )
    
    def _build_urls(self, article_data: dict) -> list:
        """Build URL list with canonical marking.
        
        Args:
            article_data: Raw article data from API
            
        Returns:
            List of ArticleUrl objects
        """
        article_href = BASE_URL.format(**article_data)
        urls = [article_url(article_href)]
        
        # Add canonical URL if different from primary
        canon_url = article_data.get("metadata", {}).get("canonical_url")
        if canon_url and canon_url != article_href:
            urls.append(article_url(canon_url, labels=[LinkLabel.LINK_CANONICAL]))
        else:
            urls[0].labels.append(LinkLabel.LINK_CANONICAL)
        
        return urls
    
    def _parse_updated_at(self, article_data: dict, created_at: datetime) -> datetime:
        """Parse updated_at timestamp or default to created_at.
        
        Args:
            article_data: Raw article data from API
            created_at: Article creation timestamp
            
        Returns:
            Updated timestamp
        """
        if updated_at_str := article_data.get("updated_at"):
            return datetime.fromisoformat(updated_at_str).astimezone(utc)
        return created_at
