import logging
import os

import dotenv
from pydantic import AnyHttpUrl, AnyUrl

from .extractor._processors import MarkdownStr
from meri.settings import settings
from .utils import setup_logging, setup_tracing
from structlog import get_logger
from .scraper import extractor

try:
    import rich_click as click
except ImportError:
    import click

dotenv.load_dotenv()

setup_logging()
tracer = setup_tracing(__package__)
logger = get_logger(__package__)

@click.group()
@click.version_option()
@click.option("--cache/--no-cache", help="Enable or disable requests cache.", default=os.getenv("REQUESTS_CACHE", True))
@tracer.start_as_current_span(f"{__name__}.cli")
def cli(cache: bool):
    if cache:
        import requests_cache
        import tempfile

        # get temp directory
        tmp_dir = tempfile.gettempdir()
        requests_cache.install_cache(f'{tmp_dir}/klikkikuri_requests_cache', expire_after=3600)


@cli.command()
@click.argument("url", required=False, type=AnyUrl)
@tracer.start_as_current_span("fetch")
def fetch(url=None):
    """
    Fetch article from URL.
    """
    if not url:
        with tracer.start_as_current_span(f"{__name__}.fetch.latest"):
            url = "https://www.iltalehti.fi/"
            logger.info("Pulling latest from %r", url)
            source = extractor(url)
            latest = source.latest()
            logger.debug("Retrieved %d latest articles", len(latest), latest=latest)

            url = latest[0]

    with tracer.start_as_current_span(f"{__name__}.fetch.article") as span:
        url = str(url)
        span.set_attribute("url", url)
        logger.info("Fetching article from %r", url)
        from meri.extractor._processors import process
        outlet = extractor(url)
        processed = process(outlet, url)
        logger.debug("Processed %d", len(processed), processed=processed)

        from rich.pretty import pprint
        from .llm import extract_interest_groups
        pprint(extract_interest_groups(processed))


if __name__ == "__main__":
    cli()
