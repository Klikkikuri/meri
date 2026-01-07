import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import random
import threading
from typing import Iterable, List, NamedTuple, Optional, Tuple, cast

import pytz
import wrapt

from .abc import ArticleTitleResponse
from .article import Article
from .pipelines.title import TitlePredictor
from .rahti import RahtiData, RahtiEntry, RahtiUrl
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


class DiscoveredArticle[T: NewsSource](NamedTuple):
    """
    Discovered article along with its source configuration.
    """
    article: Article
    source: T


class ArticleTitleData(NamedTuple):
    """
    Result of title processing for an article.
    """
    article: Article
    title: ArticleTitleResponse
    source: NewsSource


def fetch_latest[T: NewsSource](sources: Iterable[T]) -> List[DiscoveredArticle[T]]:
    """
    Fetch the latest articles from the given news sources.

    :param sources: Iterable of :class:`NewsSource` configurations.
    """

    ret: List[DiscoveredArticle[T]] = []

    # filter out disabled sources
    enabled_sources = [source for source in sources if source.enabled]

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_source, source) for source in enabled_sources]

        for future in futures:
            try:
                result = future.result()
                source = enabled_sources[futures.index(future)]
                ret.extend(DiscoveredArticle(source=source, article=article) for article in result)
            except Exception as e:
                # Log the error and continue with other sources
                logger.error("Error fetching articles: %s", e, exc_info=True)

    logger.info("Fetched %d articles", len(ret))

    return ret


def fetch_full_articles(discovered_stubs: list[DiscoveredArticle]) -> list[DiscoveredArticle]:
    """
    Fetch full articles from the given article stubs.

    Returned articles have metadata merged from the stubs, and IDs preserved.
    """

    def fetch_article(source, stub: Article) -> DiscoveredArticle:
        url = stub.get_url()

        if not url:
            raise ValueError(f"Article stub has no URL: {stub}")

        extractor = get_extractor(url)
        extracted_article = extractor.fetch_by_url(url)
        if not extracted_article:
            raise ValueError(f"Failed to fetch article from URL: {url}")

        logger.info("Fetched full article from URL: %s", str(url))

        # Merge the stub and the fetched article object.
        extracted_article.update(stub)
        # Copy id to ensure equality checks work correctly
        extracted_article._id = stub._id

        return DiscoveredArticle(source=source, article=extracted_article)

    discovered_stubs = list(discovered_stubs)
    # Shuffle article stubs to avoid overloading a single source
    random.shuffle(discovered_stubs)

    articles = []

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        logger.info("Fetching %d articles using %d threads", len(discovered_stubs), executor._max_workers)
        for stub in discovered_stubs:
            future = executor.submit(fetch_article, stub.source, stub.article)
            try:
                article = future.result()
                if not article:
                    logger.warning("Fetched article is None", extra={"url": str(stub.article.get_url())})
                    continue
                articles.append(article)
            except Exception as e:
                logger.error("Failed to fetch article: %s", e, exc_info=True, extra={"url": str(stub.article.get_url())})
                continue

    return articles


def prune_rahti(rahti: List[RahtiEntry], sources: list[NewsSource]) -> List[RahtiEntry]:
    """
    Filter RahtiEntry objects based on source-specific filtering settings.
    
    Applies max_age_days and max_num_articles per source while maintaining original order.
    When max_num_articles is applied, keeps the newest entries and removes the oldest ones.
    
    :param rahti: List of RahtiEntry objects to filter
    :param sources: List of NewsSource configurations with filtering rules
    :return: Filtered list of RahtiEntry objects in original order
    """
    # Build a lookup map from source name to source settings
    source_map = {source.name: source for source in sources if source.name and source.enabled}
    
    if not source_map:
        logger.info("No enabled sources with names found, returning empty rahti")
        return []
    
    now = datetime.now(pytz.UTC)

    # First pass: filter by max_age_days and enabled status
    filtered_entries = []
    for entry in rahti:
        # Skip entries without outlet information or from disabled/unknown sources
        if not entry.outlet or entry.outlet not in source_map:
            logger.debug("Skipping entry with unknown or disabled outlet: %r", entry.outlet)
            continue

        source = source_map[entry.outlet]
        
        # Apply max_age_days filter if set
        if source.max_age_days is not None:
            # TODO: Maybe pre-compute cutoff dates per source for efficiency
            cutoff_date = now - timedelta(days=source.max_age_days)
            if entry.updated < cutoff_date:
                continue

        filtered_entries.append(entry)
    
    # Second pass: apply max_num_articles per source, keeping newest entries
    # Group entries by source outlet
    entries_by_source: dict[str, List[tuple[int, RahtiEntry]]] = defaultdict(list)
    for idx, entry in enumerate(filtered_entries):
        entries_by_source[entry.outlet].append((idx, entry))  # type: ignore
    
    # For each source, keep only the newest max_num_articles entries
    indices_to_keep = set()
    for outlet, indexed_entries in entries_by_source.items():
        source = source_map.get(outlet)
        if source is None:
            continue
        
        if source.max_num_articles is not None:
            # Sort by updated timestamp (newest first), keep indices for ordering
            sorted_entries = sorted(indexed_entries, key=lambda x: x[1].updated, reverse=True)
            # Keep only the newest max_num_articles entries
            kept_indices = [idx for idx, _ in sorted_entries[:source.max_num_articles]]
            indices_to_keep.update(kept_indices)
        else:
            # No limit, keep all
            indices_to_keep.update(idx for idx, _ in indexed_entries)
    
    # Return entries in original order
    return [entry for idx, entry in enumerate(filtered_entries) if idx in indices_to_keep]


def remove_unhandled(articles: Iterable[Article]) -> Iterable[Article]:
    """
    Remove articles that have no handled URLs.
    """

    for article in articles:
        if not has_handled_url(article):
            logger.debug("Removing article with no handled URLs: %r", article.get_url())
            continue
        yield article


def has_handled_url(article: Article) -> bool:
    """
    Check if the article has at least one URL with a signature.
    """

    if not article.urls:
        return False
    if not any(url.signature for url in article.urls):
        return False
    return True


class RahtiCleaner:
    """
    Helper class to match Rahti entries to Articles based on URL signatures.
    """

    # Lookup map from URL signature to RahtiEntry
    map: dict[str, int]
    rahti: RahtiData

    _logger: logging.Logger

    _lock: threading.RLock

    def __init__(self, rahti: RahtiData):
        # Generate lookup map from URL signature to RahtiEntry
        self.map: dict[str, int] = {}
        self.rahti = rahti
        self._lock = threading.RLock()
        self._logger = logger.getChild(self.__class__.__name__)

        for idx, entry in enumerate(rahti.entries):
            for url in entry.urls:
                if url.sign not in self.map:
                    # Map signature to article
                    self.map[url.sign] = idx

    def find(self, entry: RahtiEntry) -> int:
        """
        Find RahtiEntry by URL signature.

        Returns the index of the matched RahtiEntry, or None if not found.
        """
        for url in entry.urls:
            if url.sign in self.map:
                return self.map[url.sign]
        return -1

    def find_by_article(self, article: Article) -> Optional[RahtiEntry]:
        """
        Find RahtiEntry matching the given Article.

        Returns the matched RahtiEntry, or None if not found.
        """
        for url in article.urls:
            if url.signature in self.map:
                idx = self.map[url.signature]
                return self.rahti.entries[idx]
        return None

    @wrapt.synchronized
    def replace(self, entry: RahtiEntry) -> Optional[RahtiEntry]:
        """
        Replace RahtiEntry with the given article's data.

        Returns the updated RahtiEntry, or None if no matching entry was found.
        """

        idx = self.find(entry)
        if idx == -1:
            self._logger.warning("Called `replace()`, but no matching entry found for %r", entry)
            return None

        old_entry = self.rahti.entries[idx]

        # Remove old references
        for url in old_entry.urls:
            if url.sign in self.map:
                del self.map[url.sign]

        # Merge data
        minimum_date = datetime.min.replace(tzinfo=pytz.UTC)
        updated = max(entry.updated or minimum_date,
                      old_entry.updated or minimum_date)

        entry.updated = updated
        entry.title = entry.title or old_entry.title
        entry.labels = list(set(old_entry.labels) | set(entry.labels))  # TODO: Merge labels more intelligently
        entry.clickbaitiness = entry.clickbaitiness or old_entry.clickbaitiness
        entry.outlet = entry.outlet or old_entry.outlet

        # Merge URLs
        existing_signs = {url.sign for url in old_entry.urls}
        for url in old_entry.urls:
            if url.sign not in existing_signs:
                entry.urls.append(url)

        # Replace in Rahti data
        self.rahti.entries[idx] = entry

        # Re-add to internal map
        for url in entry.urls:
            if url.sign not in self.map:
                self.map[url.sign] = idx

        self._mark_updated(entry)

        return old_entry


    @wrapt.synchronized
    def insert(self, entry: RahtiEntry) -> RahtiEntry:

        # Add to internal map
        self.rahti.entries.append(entry)
        idx = len(self.rahti.entries) - 1

        for url in entry.urls:
            if url.sign not in self.map:
                self.map[url.sign] = idx

        self._mark_updated(entry)

        return entry


    @wrapt.synchronized
    def upsert(self, entry: RahtiEntry) -> RahtiEntry:
        """
        Insert or replace the given RahtiEntry.
        """
        existing = self.find(entry)
        if existing != -1:
            rahti_entry = self.replace(entry)
            return rahti_entry  # type: ignore
        else:
            self._logger.debug("Inserting new Rahti entry: %r", entry.title)
            return self.insert(entry)

    def needs_updating(self, article: Article) -> bool:
        """
        Check if the given article needs updating compared to the matched Rahti entry.
        """

        # Early exit if no dates to compare
        if not (article.updated_at or article.created_at):
            self._logger.debug("Article has no updated_at or created_at, assuming needs updating: %r", article.get_url())
            return True

        rahti_entry = self.find_by_article(article)
        if not rahti_entry:
            self._logger.debug("No matching Rahti entry found for article, needs updating: %r", article.get_url())
            return True

        minimum_date = datetime.min.replace(tzinfo=pytz.UTC)
        updated = max(article.updated_at or minimum_date,
                      article.created_at or minimum_date)
        return updated > rahti_entry.updated


    def model_dump_json(self, *args, **kwargs) -> str:
        """
        Dump the Rahti data as JSON.
        """
        kwargs.setdefault("indent", 2)
        return self.rahti.model_dump_json(*args, **kwargs)


    def _mark_updated(self, entry: RahtiEntry):
        self.rahti.updated = max(datetime.min.replace(tzinfo=pytz.UTC),
            self.rahti.updated,
            entry.updated,
        )


def generate_titles(articles: list[DiscoveredArticle], old_titles: Optional[list[RahtiEntry | None]] = None) -> list[ArticleTitleData]:
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

    # For simplicity, if old_titles is not provided, create a list of None values
    if old_titles is None:
        old_titles = [None] * len(articles)  # type: ignore

    old_titles = cast(list, old_titles)

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = []
        for (article, source), old_title in zip(articles, old_titles):
            futures.append(executor.submit(predictor_run, article, old_title))

        for (article, source), future in zip(articles, futures):
            result = future.result()
            results.append(ArticleTitleData(article, result, source))

    return results


def convert_for_rahti(source: NewsSource, article: Article, title: ArticleTitleResponse) -> RahtiEntry:
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
        outlet=source.name
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
