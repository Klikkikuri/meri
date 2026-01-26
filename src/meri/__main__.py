import os
from datetime import datetime, timezone
from importlib.util import find_spec

from jinja2 import Template
from opentelemetry import trace
from sentry_sdk import monitor
from structlog import get_logger

from meri.settings import settings, init_settings

from .lautta import (
    RahtiCleaner,
    convert_for_rahti,
    fetch_full_articles,
    fetch_latest,
    generate_titles,
    has_handled_url,
    prune_rahti,
)
from .rahti import COMMIT_MESSAGE, RahtiData, create_rahti
from .scraper import get_extractor, try_setup_requests_cache
from .utils import setup_logging, setup_sentry, setup_tracing

try:
    from rich.pretty import pprint
except ImportError:
    from pprint import pprint

try:
    import rich_click as click
except ImportError:
    import click


MERI_RUN_MONITOR_SLUG = "meri_run"


logger = get_logger(__package__)
tracer = trace.get_tracer(__package__ or "__main__")

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None

@click.group()
@click.version_option()
@click.option("--cache/--no-cache", help="Enable or disable requests cache.", default=bool(os.getenv("REQUESTS_CACHE", _requests_cache_available)))
@click.option("--debug", help="Enable or disable debug mode.", default=bool(os.getenv("DEBUG", False)))
def cli(cache: bool, debug: bool):

    os.environ["DEBUG"] = "1" if debug else "0"
    os.environ["REQUESTS_CACHE"] = "1" if cache else "0"

    init_settings(debug=debug)
    setup_logging(debug=debug)
    setup_tracing()
    setup_sentry()

    if cache:
        if not _requests_cache_available:
            raise RuntimeError("requests_cache is not available, cannot enable caching.")

        try_setup_requests_cache()



@cli.command()
@click.option("--sample", is_flag=True, help="Use limited data.")
@click.option("--max-workers", type=int, default=1 if os.getenv("DEBUG") else None, help="Maximum number of workers to use for fetching articles.")
@tracer.start_as_current_span("cli.run")
@monitor(monitor_slug=MERI_RUN_MONITOR_SLUG)
def run(sample: bool, max_workers: int):

    if max_workers is not None:
        settings.MAX_WORKERS = max_workers

    # Fetch old data from Rahti
    rahti_repo = create_rahti(settings.rahti)

    hash_of_stored_file, old_data = rahti_repo.pull()

    logger.debug("Fetched old Rahti data, contains %d entries", len(old_data.entries), extra={"sha": hash_of_stored_file})

    # Fetch latest articles from sources
    latest_articles = fetch_latest(settings.sources)

    if sample:
        sorted_articles = sorted(latest_articles, key=lambda a: a.article.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        latest_articles = sorted_articles[0:5]
        logger.info("Sample mode enabled, limiting to %d articles", len(latest_articles))

    logger.info("Fetched %d latest articles from sources", len(latest_articles), extra={"sources": [s.name for s in settings.sources]})

    # Initial cleanup: filter out articles that do not need updating
    rahti = RahtiCleaner(old_data)

    latest_articles = [a for a in latest_articles if rahti.needs_updating(a.article)]

    # Early stop if no articles need updating
    if not latest_articles:
        logger.info("No articles need updating, exiting")
        return

    # Fetch full articles for those that need updating
    logger.info("After checking for updates, %d articles need updating", len(latest_articles))
    full_articles = fetch_full_articles(latest_articles)

    # free and prevent accidental usage
    del latest_articles

    nr = len(full_articles)

    # Prune out articles that are not needed to be processed further
    full_articles = [a for a in full_articles if has_handled_url(a.article)]

    logger.info("After pruning unhandled articles, %d (of %d) articles remain", len(full_articles), nr, extra={"removed": nr - len(full_articles)})

    ## Generate titles for articles
    # Collect old titles
    old_titles = []
    for a in full_articles:
        old_titles.append(rahti.find_by_article(a.article))

    titles = generate_titles(full_articles, old_titles=old_titles)

    # Match articles to old Rahti entries
    for result in titles:
        if not result.source:
            logger.warning("Article result has no source, skipping: %r", result.article.get_url())
            continue

        rahti_entry = convert_for_rahti(result.source, result.article, result.title)
        rahti.upsert(rahti_entry)

    # Final pass - remove old entries that are no longer needed
    cleaned_entries = prune_rahti(rahti.rahti.entries, settings.sources)

    # collect removed entries for logging
    removed_entries = [e for e in rahti.rahti.entries if e not in cleaned_entries]
    rahti.rahti.entries = cleaned_entries

    logger.info("After pruning Rahti entries, %d entries remain, %d removed", len(rahti.rahti.entries), len(removed_entries))

    # Prepare commit message
    articles_for_commit = [t.article for t in titles if t.source]
    titles_for_commit = [t.title for t in titles if t.source]

    commit_message = Template(COMMIT_MESSAGE).render(
        articles=articles_for_commit,
        titles=titles_for_commit,
        removed=removed_entries,
    )

    # Log prepared articles
    for t in titles:
        logger.info("Prepared article for Rahti: %r -> %r", t.article.get_url(), t.title.title)

    # Validate before pushing
    test_json = rahti.model_dump_json()
    assert RahtiData.model_validate_json(test_json)

    rahti_repo.push(
        hash_of_stored_file,
        rahti.rahti,
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
    if article.text:
        print(article.text[0:200], "...", "\n", "...", article.text[-200:])

    from .pipelines.title import TitlePredictor
    predictor = TitlePredictor()
    result = predictor.run(article)
    pprint(result.model_dump())


if __name__ == "__main__":

    with tracer.start_as_current_span("main") as span:
        cli()
