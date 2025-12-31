from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from re import Pattern
import re
from typing import List, cast


from pydantic import AnyHttpUrl
from structlog import get_logger

from meri.settings import settings
from meri.settings.newssources import NewsSource
from platformdirs import user_cache_dir

from .extractor import Outlet
from .discovery import SourceDiscoverer, registry, merge_article_lists
from .article import Article

from pydantic import HttpUrl

logger = get_logger(__name__)

DEFAULT_EXTRACTOR = "default"

@lru_cache(maxsize=128)
def get_extractor(url: AnyHttpUrl | str) -> Outlet:
    """
    Find the extractor for the given URL.

    :param url: The URL of the article.
    """

    from .extractor import get_extractors
    url = str(url)

    for outlet in get_extractors():
        outlet_urls = deepcopy(outlet.valid_url)

        if not isinstance(outlet_urls, list):
            outlet_urls = [outlet_urls]

        # Convert into regex patterns if needed
        for i, outlet_url_rule in enumerate(outlet_urls):
            match outlet_url_rule:
                case Pattern():
                    continue
                case str():
                    outlet_urls[i] = re.compile(r"^" + re.escape(outlet_url_rule))  # type: ignore
                case _:
                    raise ValueError(f"Invalid outlet URL rule type: {type(outlet_url_rule)}")

        for outlet_url_rule in outlet_urls:
            if outlet_url_rule.match(url):
                logger.debug("Matched outlet %s for URL %s", outlet.name, url)
                return outlet

    raise ValueError(f"No outlet parse found for URL {url!r}")


def get_user_agent():
    """
    Return the user-agent string to be used for requests.
    """

    return settings.BOT_USER_AGENT


def try_setup_requests_cache():
    """
    Try to setup requests cache.

    This function will check if the :py:mod:`requests_cache` module is installed, and if it is, it will
    setup the cache for the requests library.
    """

    if not settings.REQUESTS_CACHE:
        logger.debug("Requests cache is disabled, skipping setup")
        return

    try:
        import requests_cache
    except ImportError:
        logger.warning("requests_cache is not installed, skipping cache setup. Install it with `pip install requests_cache`.")
        return

    # Check if the cache is already set up
    if requests_cache.is_installed():
        logger.debug("Cache already set up, skipping setup")
        return

    # Setup the cache
    cache_path = Path(user_cache_dir(__package__), "requests-cache")

    requests_cache.install_cache(
        cache_name=str(cache_path),
    )
    logger.debug("Cache set up at %s", cache_path)


def get_discoverer(source: NewsSource) -> SourceDiscoverer:
    """
    Get the discoverer for the given news source.

    :param source: The news source to get the discoverer for.
    :return: Discoverer instance for the source
    :raises ValueError: If no discoverer is found for the source type
    """
    discoverer = registry.get_instance(source.type)
    
    if discoverer is None:
        available = registry.list_names()
        raise ValueError(
            f"No discoverer found for source {source.name!r} with type {source.type!r}. "
            f"Available discoverers: {', '.join(available)}"
        )
    
    return discoverer


def discover_articles(source: NewsSource) -> list[Article]:
    """
    Discover articles from a news source and remove duplicates.

    This function fetches articles from all URLs configured in the news source
    using the appropriate discoverer, then merges the results and removes
    any duplicate articles.

    :param source: The news source to discover articles from.
    :return: List of unique Article objects discovered from the source.
    :raises ValueError: If no discoverer is found for the source type.
    """
    if not source.enabled:
        logger.info("News source %s is disabled, skipping discovery", source.name)
        return []

    discoverer = get_discoverer(source)
    article_lists = []

    for url in source.url:
        try:
            logger.info("Discovering articles from %s using %s discoverer", url, source.type)
            # Convert to HttpUrl if needed

            http_url = HttpUrl(str(url))
            # Pass language if available
            kwargs = {}
            if source.language:
                kwargs['language'] = source.language
            articles = discoverer.discover(http_url, **kwargs)
            
            # Set outlet name to source name if not already set by discoverer
            for article in articles:
                if not article.meta.get('outlet'):
                    article.meta['outlet'] = source.name
            
            article_lists.append(articles)
            logger.debug("Discovered %d articles from %s", len(articles), url)
        except Exception as e:
            logger.error(
                "Failed to discover articles from URL",
                url=url,
                source=source.name,
                error=str(e),
                exc_info=True
            )
            continue

    # Merge all article lists and remove duplicates
    unique_articles = merge_article_lists(*article_lists)
    logger.info(
        "Discovered %d unique articles from %s (%d total before deduplication)",
        len(unique_articles),
        source.name,
        sum(len(articles) for articles in article_lists)
    )

    return unique_articles
