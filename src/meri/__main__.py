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

    # NOTE HACK: Manually set the env variable in container based on .env file.
    with open(".env", "r") as fp:
        lines = fp.readlines()
        kps = { s.split("=")[0] : s.split("=")[1] for s in lines }
        print(kps)
        os.environ["OPENAI_API_KEY"] = kps["OPENAI_API_KEY"]

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
        # FIXME: This function does not exist.
        #from .llm import extract_interest_groups
        #pprint(extract_interest_groups(processed))
    from .extractor.iltalehti import Iltalehti
    from .pipelines.title import TitlePredictor
    processor = Iltalehti()
    links = processor.latest()
    pprint(links)
    trafilatura_extractor = processor.processors[1]

    data = {}
    # TODO: Run for all links.
    link = links[0]
    article_object = trafilatura_extractor(link)
    pprint(article_object)
    predictor = TitlePredictor()
    result = predictor.run(article_object)
    pprint(result)
    # TODO: Convert results into the public Klikkikuri data format.
    # TODO: Figure out this Wasm mess.
    #suola = Instantiate("./suola/build/wasi.wasm")
    for x in [(result, link)]:
        # TODO: Wasm thingy.
        #link_hash = suola.exports.GetSignature(link)
        import hashlib
        m = hashlib.sha256(bytes(link, encoding="utf-8"))
        link_hash = m.hexdigest()
        if link_hash not in data:
            data[link_hash] = {
                "title": result.title,
                "reason": result.evidence.content,
                "labels": [], # TODO: Put the Article's labels here?
            }
        else:
            raise Exception(f"Hash for article {link} already exists for article {data[link_hash]}")
        # TODO: Handle the linking to repeated articles with differing normalized URLs e.g.:
        # { "1Ex15T": { ... }, "1AmN3W": { "canonical": "1Ex15T" }, }
    pprint(data)
    # TODO: Push the data to rahti.



if __name__ == "__main__":
    cli()
