"""
Utility functions for the discovery module.
"""

from typing import Iterable
from structlog import get_logger

from meri.article import Article

logger = get_logger(__name__)


def merge_article_lists(*article_lists: Iterable[Article]) -> list[Article]:
    """
    Merge multiple lists of articles into a single list, removing duplicates based on article URLs.

    :param article_lists: Multiple iterables containing Article objects.
    :return: A single list of unique Article objects.
    """
    seen_urls = set()
    seen_ids = set()
    merged_articles = []

    for article_list in article_lists:
        for article in article_list:
            # Check if any of the article's URLs have been seen before
            if any(url.href in seen_urls for url in article.urls):
                continue  # Skip duplicate articles

            # Add the article's URLs to the seen set
            for url in article.urls:
                seen_urls.add(url.href)

            article_id = article.meta.get("id", None)
            if article_id is not None:
                # Check if the article's ID has been seen before
                if article_id in seen_ids:
                    logger.debug(
                        "Skipping article with duplicate ID",
                        article_id=article_id,
                        title=article.meta.get("title", "")
                    )
                    continue  # Skip duplicate articles

                # Add the article's ID to the seen set
                seen_ids.add(article_id)

            merged_articles.append(article)

    return merged_articles
