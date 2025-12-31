import os
from datetime import datetime, timezone
from importlib.util import find_spec
from typing import List

from jinja2 import Template
from opentelemetry import trace
from rich.pretty import pprint  # TODO: remove from production code
from structlog import get_logger

from meri.article import Article
from meri.settings import settings

from .lautta import (
    Matcher,
    convert_for_rahti,
    fetch_full_articles,
    fetch_latest,
    generate_titles,
    prune_partition,
    remove_unhandled,
)
from .rahti import COMMIT_MESSAGE, RahtiData, RahtiEntry, rahti
from .scraper import get_extractor, try_setup_requests_cache
from .utils import setup_logging, setup_tracing

try:
    import rich_click as click
except ImportError:
    import click


logger = get_logger(__package__)
tracer = trace.get_tracer(__package__ or "__main__")

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None


@click.group()
@click.version_option()
@click.option("--cache/--no-cache", help="Enable or disable requests cache.", default=bool(os.getenv("REQUESTS_CACHE", _requests_cache_available)))
@click.option("--debug", help="Enable or disable debug mode.", default=bool(os.getenv("DEBUG", False)))
def cli(cache: bool, debug: bool):
    if debug:
        os.environ["DEBUG"] = "1"

    setup_logging(debug=debug)
    setup_tracing()

    if cache:
        if not _requests_cache_available:
            raise RuntimeError("requests_cache is not available, cannot enable caching.")

        try_setup_requests_cache()

    os.environ["REQUESTS_CACHE"] = "1" if cache else "0"


@cli.command()
@tracer.start_as_current_span("cli.run")
def run():

    now = datetime.now(timezone.utc)

    span = trace.get_current_span()

    # Fetch old data from Rahti
    rahti_repo = rahti(settings.rahti)

    hash_of_stored_file, old_data = rahti_repo.pull()

    span.set_attribute("rahti.url", str(settings.rahti.url))
    span.set_attribute("rahti.stored_file_sha", hash_of_stored_file)
    span.set_attribute("rahti.old_data_entry_count", len(old_data.entries))
    logger.debug("Fetched old Rahti data, contains %d entries", len(old_data.entries), extra={"sha": hash_of_stored_file})

    # Fetch latest articles from sources
    latest_articles = fetch_latest(settings.sources)
    latest_articles = list(remove_unhandled(latest_articles))
    logger.info("Fetched %d latest articles from sources", len(latest_articles), extra={"sources": [s.name for s in settings.sources]})

    # if settings.DEBUG:
    #     latest_articles = random.sample(latest_articles, min(5, len(latest_articles)))
    #     logger.debug("Debug mode enabled, limiting to %d articles", len(latest_articles))

    # Check which articles require updating
    # Use enumerate to keep track of indices for matching with Rahti entries
    updated_articles_map: dict[Article, int] = {}

    # Initial cleanup: filter out articles that do not need updating
    matcher = Matcher(latest_articles)
    for idx, rahti_entry in enumerate(old_data.entries):
        article = matcher.match(rahti_entry)

        # Skip if no matching article found
        if not article: continue
        if article not in latest_articles:
            logger.debug("Matched article not in latest articles, skipping", extra={"index": idx, "url": article.get_url()})
            continue

        updated = article.updated_at or article.created_at or datetime.now(timezone.utc)
        if updated > rahti_entry.updated:
            logger.debug("Article updated: %r (was %r, now %r)", article.get_url(), rahti_entry.updated, article.updated_at, extra={"index": idx})
            updated_articles_map[article] = idx
        else:
            # Remove from latest_articles to avoid re-processing
            logger.debug("Article not updated: %r", article.get_url(), extra={"index": idx})
            latest_articles.remove(article)

    latest_articles = fetch_full_articles(latest_articles)

    # Remove articles that can't be hash-matched to Rahti entries
    latest_articles = [a for a in latest_articles if any(url.signature for url in a.urls)]

    # Early stop if no articles to process
    if not latest_articles:
        logger.info("No updated articles to process, exiting")
        return

    logger.debug("Fetched full articles for %d updated articles", len(latest_articles))

    # Collect old titles for comparison
    old_entries: list[RahtiEntry | None] = []

    updated_articles = list(updated_articles_map.keys())

    for article in latest_articles:
        v = None
        if article in updated_articles:
            idx = updated_articles_map[article]
            v = old_data.entries[idx]
        old_entries.append(v)

    # Process titles
    logger.info("Processing titles for %d articles", len(latest_articles))
    title_results = generate_titles(latest_articles, old_entries)

    # Partition results
    articles = []
    titles = []
    for e in title_results:
        articles.append(e.article)
        titles.append(e.title)

    if settings.DEBUG:
        pprint(titles)

    valid_rahti, expired_rahti = prune_partition(old_data.entries)

    commit_message = Template(COMMIT_MESSAGE).render(
        articles=articles,
        titles=titles,
        removed=expired_rahti,
    )

    new_entries: List[RahtiEntry] = []

    # Store results back to Rahti
    for article, title in title_results:
        logger.info("Processed article", url=article.get_url(), title=title.title)
        rahti_entry = convert_for_rahti(article, title)
        # If article is in rahti already, replace the old entry
        print(article.__hash__())

        if article in updated_articles_map.keys():
            idx = updated_articles_map[article]
            logger.debug("Merging updated article %r into existing Rahti entry", old_data.entries[idx].title)
            old_data.entries[idx] = rahti_entry
        else:
            new_entries.append(rahti_entry)

    rahti_cargo = old_data.model_copy(update={
        "status": "ok",
        "updated": now,
        "entries": new_entries + valid_rahti,
    }, deep=True)

    # Validate before pushing
    test_json = rahti_cargo.model_dump_json()
    RahtiData.model_validate_json(test_json)

    rahti_repo.push(
        hash_of_stored_file,
        rahti_cargo,
        commit_message,
    )


@cli.command()
def list_sources():
    """
    List available extractors.
    """
    import meri.settings

    for source in meri.settings.settings.sources:
        print(f"Extractor: {source.name} (weight={source})")


@cli.command()
@click.argument("url")
def test(url):
    extractor = get_extractor(url)
    article = extractor.fetch_by_url(url)
    print(article.text[0:200], "...", "\n", "...", article.text[-200:])

    _, p = generate_titles([article])[0]
    pprint(p.model_dump())


if __name__ == "__main__":

    with tracer.start_as_current_span("main") as span:
        cli()
