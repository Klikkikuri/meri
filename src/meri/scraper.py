from pathlib import Path
from re import Pattern
from urllib.parse import urlparse

from pydantic import AnyHttpUrl
from structlog import get_logger

from meri.settings import settings
from platformdirs import user_cache_dir

from .extractor import Outlet

logger = get_logger(__name__)


def get_extractor(url: AnyHttpUrl) -> Outlet:
    """
    Find the extractor for the given URL.

    :param url: The URL of the article.
    """

    from .extractor import get_extractors

    article_url_parts = urlparse(str(url))

    # Find the outlet that matches the URL
    # Warning: there be dragons here
    for outlet in get_extractors():
        outlet_urls = outlet.valid_url
        if not isinstance(outlet_urls, list):
            outlet_urls = [outlet_urls]

        for outlet_url_rule in outlet_urls:
            logger.debug("Checking outlet %s rule %s", outlet.name, outlet_url_rule)
            if isinstance(outlet_url_rule, Pattern):
                if outlet_url_rule.match(url):
                    logger.debug("Matched `re.Pattern` outlet %s for URL %s", outlet.name, url)
                    return outlet

                continue  # URL part does not match, try next outlet
            elif isinstance(outlet_url_rule, str):
                logger.debug("Converting string to ParseResult")
                outlet_url_rule = urlparse(outlet_url_rule)
                if outlet_url_rule.path == "/":
                    # Drop the path to match any path
                    outlet_url_rule = outlet_url_rule._replace(path="")

            # Compare the parts of the URL that are defined in the matching URL
            for key in outlet_url_rule._fields:
                if outlet_part := getattr(outlet_url_rule, key):
                    logger.debug("Checking outlet %s for URL %s part %s", outlet.name, url, key)
                    if outlet_part != getattr(article_url_parts, key):
                        logger.debug("Outlet url part %r does not match %r, try next outlet", outlet_part, getattr(article_url_parts, key))
                        break  # URL part does not match, try next outlet
            else:
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
