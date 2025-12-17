"""
Kontio API extractor for fetching full article content.

TODO: The text extracted _might_ contain HTML tags - this is to be verified.

TODO: Convert the HTML into markdown for consistency with other extractors.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from urllib.parse import urlparse
import uuid
from typing import Final

from .client import KontioApiClient, client
from .types import KontioApiParams
from pytz import utc
from structlog import get_logger


from ...abc import ArticleLabels, ArticleMeta
from meri.article import Article


from .._common import Outlet

logger = get_logger(__name__)

# Kontio API constants
ACCESS_LEVEL_FREE: Final[str] = "free"
ACCESS_LEVEL_PAID: Final[str] = "paid"


class KontioExtractor(Outlet, ABC):
    """
    Extract full article content from Kontio API.

    This is a base class for Kontio-based news outlets, part of Keskisuomalainen media group.
    Subclasses must implement :meth:`get_api_params` to provide publication-specific configuration.
    """

    # API endpoint template for fetching article details
    API_BASE = "https://api.prod.kontio.diks.fi/api/v1/publications/{publication}/sections/{section}/stories/{article_id}"
    "API endpoint template for fetching article details. Fields filled from :py:class:`KontioApiParams`."

    # Block types in storyline
    BLOCK_TYPE_HEADER: Final[str] = "header"
    BLOCK_TYPE_RICH_TEXT: Final[str] = "rich_text"
    BLOCK_TYPE_QUOTE: Final[str] = "quote"
    BLOCK_TYPE_TEXT: Final[str] = "text"

    _api: KontioApiClient

    def __init__(self) -> None:
        self._api = client()
        super().__init__()

    @abstractmethod
    def get_api_params(self, article: Article) -> KontioApiParams:
        """Extract API parameters from article.

        Subclasses must implement this to provide publication-specific logic
        for extracting publication, section, and article_id.

        :param article: Article stub from discoverer
        :returns: KontioApiParams with publication, section, and article_id
        """
        raise NotImplementedError()

    def fetch_by_article(self, article: Article) -> Article:
        """
        Fetch full article content using the Kontio API.

        :param article: Article stub from discoverer containing metadata
        :returns: Article with full text content extracted from API
        """
        logger.debug("Fetching full article content via Kontio API", article_id=article.meta.get("id"))

        # Get API parameters from subclass or generic implementation
        params = self.get_api_params(article)

        # Build API URL
        api_url = self.API_BASE.format(**params._asdict())

        logger.debug("Fetching from Kontio API", api_url=api_url)

        # Fetch article data from API
        response = self._api.get(api_url, timeout=15)

        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            logger.error("Kontio API returned ok=false", api_url=api_url)
            raise ValueError("Kontio API request failed")

        # Extract article content
        full_article = self._parse_article_data(data.get("data", {}), article)

        return full_article

    def _parse_article_data(self, data: dict, original_article: Article) -> Article:
        """Parse full article data from API response.

        :param data: API response data dictionary
        :param original_article: Original article stub from discoverer
        :returns: Article with full content and updated metadata
        """
        meta_data = data.get("meta", {})
        storyline = data.get("storyline", [])

        # Extract text content from storyline
        text_content = self._extract_text_from_storyline(storyline)

        if not text_content:
            logger.warning("No text content found in storyline", article_id=meta_data.get("id"))
            # Return original article if we can't extract content
            return original_article

        # Build updated metadata
        meta = ArticleMeta(original_article.meta)

        # Update with additional metadata from full article if available
        if headline := meta_data.get("headline"):
            meta["title"] = headline

        # Parse timestamps with timezone awareness
        created_at = original_article.created_at
        updated_at = original_article.updated_at

        if published_at := meta_data.get("published_at"):
            created_at = datetime.fromisoformat(published_at).astimezone(utc)

        if updated_at_str := meta_data.get("updated_at"):
            updated_at = datetime.fromisoformat(updated_at_str).astimezone(utc)
        elif not updated_at:
            updated_at = created_at

        # Check access level for paywall
        labels = list(original_article.labels)
        access_level = meta_data.get("access_level", ACCESS_LEVEL_FREE)
        if access_level != ACCESS_LEVEL_FREE and ArticleLabels.PAYWALLED not in labels:
            labels.append(ArticleLabels.PAYWALLED)
            logger.debug("Article has restricted access", access_level=access_level)

        # Check for sponsored content
        if advertiser := meta_data.get("advertiser"):
            if ArticleLabels.SPONSORED not in labels:
                labels.append(ArticleLabels.SPONSORED)
                logger.debug("Article is sponsored", advertiser=advertiser)

        # Build full article
        full_article = Article(
            text=text_content,
            meta=meta,
            labels=labels,
            urls=original_article.urls,
            created_at=created_at,
            updated_at=updated_at,
        )

        return full_article

    def _extract_text_from_storyline(self, storyline: list[dict]) -> str:
        """
        Extract text content from storyline blocks.

        The storyline contains various block types. We focus on 'rich_text' blocks
        which contain the actual article text content.

        :param storyline: List of storyline block dictionaries from API
        :returns: Extracted text content joined with double newlines
        """
        text_blocks: list[str] = []

        for block in storyline:
            block_type = block.get("type")

            match block_type:
                case "header":
                    # Extract headline/ingress from header
                    header_data = block.get("data", {}).get("data", {})
                    if ingress := header_data.get("ingress"):
                        text_blocks.append(ingress)

                case "rich_text":
                    # Extract text from rich_text content blocks
                    content_items = block.get("data", {}).get("content", [])
                    for item in content_items:
                        if item.get("type") == self.BLOCK_TYPE_TEXT:
                            if text := item.get("data", {}).get("content"):
                                text_blocks.append(text)

                case "quote":
                    # Extract quote text
                    quote_data = block.get("data", {})
                    if quote_text := quote_data.get("quote"):
                        text_blocks.append(f'"{quote_text}"')
                    if attribution := quote_data.get("attribution"):
                        text_blocks.append(f"— {attribution}")

                case _:
                    # Skip ad_container, story_list_tail_container, and other non-content blocks
                    pass

        # Join all text blocks with double newlines for paragraph separation
        return "\n\n".join(text_blocks)


class KSMLExtractor(KontioExtractor):
    """Extractor for Keskisuomalainen (KSML) articles via Kontio API."""

    name = "KSML"
    valid_url = r"https://www\.ksml\.fi/"
    weight = 60
    
    # KSML API publication identifier
    PUBLICATION_ID = "ksml"

    def __init__(self) -> None:
        super().__init__()
        self._api.headers.update({
            # KSML-specific header if needed
            "x-kontio-app-id": self.PUBLICATION_ID,
        })

    def get_api_params(self, article: Article) -> KontioApiParams:
        """Extract API parameters for KSML articles.

        :param article: Article stub from discoverer
        :returns: KontioApiParams with publication, section, and article_id
        """
        article_id = article.meta.get("id")
        if not article_id:
            raise ValueError("Article missing ID for KSML API extraction")

        url = article.get_url()
        if not url:
            raise ValueError("Article missing URL")

        # KSML URL format: https://www.ksml.fi/{section}/{id}
        parsed_url = urlparse(str(url))
        path_parts = parsed_url.path.rstrip('/').split('/')
        if len(path_parts) < 2:
            raise ValueError(f"Cannot parse KSML article URL: {url}")

        section = path_parts[-2]
        
        # KSML always uses "ksml" as publication identifier
        return KontioApiParams(
            publication=self.PUBLICATION_ID,
            section=section,
            article_id=str(article_id),
        )
