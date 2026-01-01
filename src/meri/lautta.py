import logging
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple, cast

import pytz

from .abc import ArticleTitleResponse
from .article import Article
from .pipelines.title import TitlePredictor
from .rahti import RahtiEntry, RahtiUrl
from .scraper import discover_articles, get_extractor
from .settings import settings
from .settings.newssources import NewsSource
from .utils import setup_logging

MAX_PARALLEL_FETCHES = 3

logger = logging.getLogger(__name__)

def fetch_source(source: NewsSource) -> List[Article]:
    """
    Fetch articles from a single news source.

    Does initial filtering based on source settings like max_age_days and max_num_articles.
    """
    articles: List[Article] = []

    logger.info("Fetching new articles from source: %r", source.name or 'Unnamed Source', extra={"source": source})

    articles = discover_articles(source)
    # Sort by updated time, newest first
    articles.sort(key=lambda a: a.updated_at or a.created_at or 0, reverse=True)

    articles = list(remove_unhandled(articles))

    # Remove old articles based on max_age_days
    if source.max_age_days is not None:
        from datetime import datetime, timedelta

        now = datetime.now(pytz.UTC)
        cutoff_date = now - timedelta(days=source.max_age_days)
        original_count = len(articles)
        articles = [
            article for article in articles
            # now is a fallback in case both dates are None
            if (article.updated_at or article.created_at or now) >= cutoff_date
        ]
        logger.info(
            "Filtered articles from source %r by max_age_days=%d: %d -> %d",
            source.name or "Unnamed Source",
            source.max_age_days,
            original_count,
            len(articles),
            extra={"source": source, "max_age_days": source.max_age_days, "original_count": original_count, "filtered_count": len(articles)},
        )

    # Limit number of articles if max_num_articles is set
    if source.max_num_articles is not None and len(articles) > source.max_num_articles:
        logger.info(
            "Limiting articles from source %r to max_num_articles=%d (was %d)",
            source.name or "Unnamed Source",
            source.max_num_articles,
            len(articles),
            extra={"source": source, "max_num_articles": source.max_num_articles, "original_count": len(articles)},
        )
        articles = articles[: source.max_num_articles]

    return articles


def fetch_latest(sources: Iterable[NewsSource]) -> List[Article]:
    """
    Fetch the latest articles from the given news sources.
    """

    articles: List[Article] = []

    # filter out disabled sources
    enabled_sources = [source for source in sources if source.enabled]

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_source, source) for source in enabled_sources]

        for future in futures:
            try:
                result = future.result()
                articles.extend(result)
            except Exception as e:
                # Log the error and continue with other sources
                logger.error("Error fetching articles: %s", e, exc_info=True)

    # Remove duplicate articles based on URL
    fetched_articles_len = len(articles)
    articles = list(remove_duplicate_articles(articles))
    logger.info("Fetched %d articles, %d unique after deduplication", fetched_articles_len, len(articles))

    return articles


def prune_partition(rahti: List[RahtiEntry]) -> Tuple[List[RahtiEntry], List[RahtiEntry]]:
    """
    Prune items from rahti that are too old.
    """
    now = datetime.now(pytz.UTC)
    
    # Ugly hack: Until we keep track of sources also, use oldest possible max_age_days
    max_age = max((
        source.max_age_days for source in settings.sources
        if source.enabled and source.max_age_days is not None
    ), default=None)
    if max_age is None:
        logger.info("No max_age_days set for any source, skipping pruning")
        return rahti, []  # Nothing to prune

    cutoff_date = now - timedelta(days=max_age)

    # Split into valid and expired entries
    valid = []
    expired = []

    for entry in rahti:
        if entry.updated >= cutoff_date:
            valid.append(entry)
        else:
            expired.append(entry)
    logger.info("Pruned %d expired entries older than %d days", len(expired), max_age)
    return valid, expired


def fetch_full_articles(article_stubs: list[Article]) -> list[Article]:
    """
    Fetch full articles from the given article stubs.

    Returned articles have metadata merged from the stubs, and IDs preserved.
    """

    def fetch_article(stub: Article) -> Article:
        url = stub.get_url()

        if not url:
            raise ValueError(f"Article stub has no URL: {stub}")

        extractor = get_extractor(url)
        extracted_article = extractor.fetch_by_url(url)

        # Merge the stub and the fetched article object.
        extracted_article.update(stub)
        # Copy id to ensure equality checks work correctly
        extracted_article._id = stub._id

        return extracted_article

    articles = []

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        logger.info("Fetching %d articles using %d threads", len(article_stubs), executor._max_workers)
        for stub in article_stubs:
            future = executor.submit(fetch_article, stub)
            try:
                article = future.result()
                if not article:
                    logger.warning("Fetched article is None", url=str(stub.get_url()))
                    continue
                articles.append(article)
            except Exception as e:
                logger.error("Failed to fetch article: %s", e, exc_info=True, extra={"url": str(stub.get_url())})
                continue

    return articles


def remove_duplicate_articles(articles: List[Article]) -> Iterable[Article]:
    """
    Remove duplicate articles based on their URLs or IDs.

    Yields unique articles.
    """
    seen_urls = set()
    seen_ids = set()

    for article in articles:
        # Check if any of the article's URLs have been seen before
        if any(url.href in seen_urls for url in article.urls):
            continue  # Skip duplicate articles

        if any(url.signature in seen_urls for url in article.urls):
            continue  # Skip duplicate articles

        # Add the article's URLs to the seen set
        for url in article.urls:
            seen_urls.add(url.href)

        # Comparing by article ID if available
        # FIXME: ID's may not be unique across sources, but works for sources we've been using so far
        article_id = article.meta.get("id", None)
        if article_id is not None:
            # Check if the article's ID has been seen before
            if article_id in seen_ids:
                logger.debug(
                    "Skipping article with duplicate ID %r (%r)",
                    article_id,
                    article.meta.get("title", "")
                )
                continue  # Skip duplicate articles

            # Add the article's ID to the seen set
            seen_ids.add(article_id)

        yield article


def remove_unhandled(articles: List[Article]) -> Iterable[Article]:
    """
    Remove articles that have no handled URLs.
    """

    for article in articles:
        # If no signature or href, skip the article
        if not article.urls:
            logger.debug(
                "Skipping article with no URLs (%r)",
                article.get_url()
            )
            continue
        if not any(url.signature for url in article.urls):
            logger.debug(
                "Skipping article with no URL signatures (%r)",
                article.get_url()
            )
            continue
        yield article


class Matcher:
    """
    Helper class to match Rahti entries to Articles based on URL signatures.
    """
    def __init__(self, rahti: List[Article]) -> None:
        self.map = {}
        for entry in rahti:
            for url in entry.urls:
                self.map[url.signature] = entry

    def match(self, rahti_entry: RahtiEntry) -> Article | None:
        for url in rahti_entry.urls:
            if url.sign in self.map:
                return self.map[url.sign]
        return None


def filter_update_required(old: List[RahtiEntry], new: List[Article]) -> Iterable[Tuple[bool, Article]]:
    """
    Determine if article requires updating.

    Yields tuples of (is_update: bool, article: Article).
    """

    # When signatures match, use the newer-released article's entry.
    # Initially assume that we are just saving the old article already stored
    # and now pulled from storage for possible update.

    # Convert into lookup tables based on URL signatures
    old_lookup: dict[str, RahtiEntry] = {}
    now = datetime.now(pytz.UTC)
    for entry in old:
        for url in entry.urls:
            old_lookup[url.sign] = entry

    for article in new:
        matched_entry: RahtiEntry
        for url in article.urls:
            # Check if any URL signature matches an old entry
            if url.signature in old_lookup:
                matched_entry: RahtiEntry = old_lookup[url.signature]
                break
        else:
            # No matching entry found
            continue

        # Matching entry found – check if update is needed
        updated = article.updated_at or article.created_at or now

        # Check if the article has been updated since last stored
        if updated > matched_entry.updated:
            logger.info(
                "Article updated: %r (was %r, now %r)",
                article.get_url(),
                matched_entry.updated,
                article.updated_at
            )

            yield (True, article)
        else:
            # No update needed, keep the old entry
            yield (False, article)


ArticleTitleData = namedtuple("ArticleTitleData", ["article", "title"])
"""Needed for passing article URLs along with title processing results"""

def generate_titles(articles: list[Article], old_titles: Optional[list[RahtiEntry | None]] = None) -> list[ArticleTitleData]:
    """
    Process articles for titles.
    """
    results = []

    def predictor_run(article: Article, old_title: RahtiEntry | None) -> ArticleTitleResponse:
        predictor = TitlePredictor()
        kwargs = {}
        if old_title:
            kwargs["rahti"] = old_title
        return predictor.run(article, **kwargs)  # type: ignore

    # For siplicity, if old_titles is not provided, create a list of None values
    if old_titles is None:
        old_titles = [None] * len(articles)  # type: ignore

    old_titles = cast(list, old_titles)


    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = []
        for article, old_title in zip(articles, old_titles):
            futures.append(executor.submit(predictor_run, article, old_title))

        for article, future in zip(articles, futures):
            result = future.result()
            results.append(ArticleTitleData(article, result))

    return results


def convert_for_rahti(article: Article, title: ArticleTitleResponse) -> RahtiEntry:
    """
    Convert an Article and its title data into a RahtiEntry.
    """
    minimum_date = datetime.min.replace(tzinfo=pytz.UTC)

    updated = max(article.updated_at or minimum_date,
                  article.created_at or minimum_date)

    entry = RahtiEntry(
        updated=updated,
        urls=[
            RahtiUrl(
                sign=url.signature,
                labels=url.labels,
            ) for url in article.urls if url.signature
        ],
        title=title.title,
        clickbaitiness=title.original_title_clickbaitiness,
        labels=article.labels,
    )
    return entry

if __name__ == "__main__":
    from .settings import settings

    setup_logging(True)

    latest_articles = fetch_latest(settings.sources)
    latest_articles = list(remove_unhandled(latest_articles))
    from pprint import pprint
    #pprint(settings.model_dump())

    pprint(latest_articles)
