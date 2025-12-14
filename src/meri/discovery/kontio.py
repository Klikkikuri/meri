"""Kontio API discoverer for fetching articles from Kontio-based news outlets."""

from datetime import datetime

import requests
from pydantic import HttpUrl
from pytz import utc
from structlog import get_logger

from meri.abc import ArticleLabels, ArticleMeta, article_url
from meri.article import Article

from ._base import SourceDiscoverer
from ._registry import registry

logger = get_logger(__name__)

# Publication configurations
PUBLICATIONS = {
    "ksml": {
        "name": "Keskisuomalainen",
        "url_template": "https://www.ksml.fi/{section}/{id}",
    },
}


@registry.register(["kontio", "ksml"])
class KontioDiscoverer(SourceDiscoverer):
    """Discover articles from Kontio API-based news outlets."""

    def discover(self, source_url: HttpUrl, **kwargs) -> list[Article]:
        """Fetch latest articles from Kontio API.

        Args:
            source_url: Direct Kontio API feed URL
            **kwargs: Optional parameters including 'language' and 'outlet'

        Returns:
            List of Article objects with metadata
        """
        language = kwargs.get('language', 'fi')
        outlet = kwargs.get('outlet')

        logger.debug("Discovering articles from Kontio API: %s", source_url)

        response = requests.get(str(source_url), timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            logger.error("Kontio API returned ok=false", url=source_url)
            return []

        articles = []
        for group in data.get("data", {}).get("groups", []):
            for child in group.get("children", []):
                articles.extend(self._parse_container(child, language, outlet))

        logger.debug("Discovered %d articles from Kontio API", len(articles))
        return articles

    def _parse_container(self, container: dict, language: str, outlet: str | None) -> list[Article]:
        """Parse a story_list_container from API response."""
        if container.get("type") != "story_list_container":
            return []

        articles = []
        for story_card in container.get("data", {}).get("stories", []):
            story_data = story_card.get("data", {}).get("story", {}).get("data", {})
            if article := self._parse_story(story_data, language, outlet):
                articles.append(article)

        return articles

    def _parse_story(self, story_data: dict, language: str, outlet: str | None) -> Article | None:
        """Parse a story into an Article."""
        if not (article_id := story_data.get("id")) or not (headline := story_data.get("headline")):
            return None

        if not (published_at := story_data.get("published_at")):
            logger.warning("Article missing published_at: %r", headline)
            return None

        # Parse timestamps
        created_at = datetime.fromisoformat(published_at).astimezone(utc)
        updated_at = (
            datetime.fromisoformat(story_data["updated_at"]).astimezone(utc)
            if story_data.get("updated_at")
            else created_at
        )

        # Validate timestamps
        now = datetime.now(utc)
        if created_at > now or updated_at < created_at:
            logger.warning("Invalid timestamps for article: %s", headline)
            return None

        # Build URL
        url = self._build_url(article_id, story_data.get("section", ""), story_data.get("publication", ""))
        if not url:
            logger.warning("Unable to build URL for article %s", article_id)
            return None

        # Build metadata
        meta = ArticleMeta({
            "title": headline,
            "outlet": outlet or self._get_outlet_name(story_data.get("publication", "")),
            "language": language,
            "id": str(article_id),
        })

        # Add authors if available
        if authors := story_data.get("authors", []):
            if author_names := [a.get("full_name") for a in authors if a.get("full_name")]:
                meta["authors"] = author_names

        # Detect sponsored content
        labels = []
        if advertiser := story_data.get("advertiser"):
            labels.append(ArticleLabels.SPONSORED)
            logger.debug("Sponsored: %s (by %s)", headline, advertiser.get("name", "Unknown"))

        return Article(
            text=None,
            meta=meta,
            urls=[url],
            labels=labels,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _build_url(self, article_id: str, section: str, publication: str):
        """Build article URL from publication configuration."""
        if pub := PUBLICATIONS.get(publication):
            if template := pub.get("url_template"):
                return article_url(template.format(section=section, id=article_id))
        return None

    def _get_outlet_name(self, publication: str) -> str:
        """Get outlet name from publication identifier."""
        return PUBLICATIONS.get(publication, {}).get("name", publication)


if __name__ == "__main__":
    import pprint

    discoverer = KontioDiscoverer()
    articles = discoverer.discover(HttpUrl("https://api.prod.kontio.diks.fi/api/v1/publications/ksml/feeds/paajutut?page=1"))
    for article in articles:
        #print(article.meta["title"], article.urls)
        pprint.pprint(article.model_dump())


