import base64
from datetime import datetime, timezone
import hashlib
import logging
import os
import json

import dotenv
from pydantic import AnyHttpUrl, AnyUrl
from rich.pretty import pprint
import requests

from .extractor._processors import MarkdownStr
from meri.settings import settings
from meri.extractor._processors import process
from meri.abc import ArticleTitleResponse, Article
from .utils import setup_logging, setup_tracing
from structlog import get_logger
from .scraper import extractor
from .extractor.iltalehti import Iltalehti
from .pipelines.title import TitlePredictor
from meri.extractor._extractors import trafilatura_extractor


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
        requests_cache.install_cache(f"{tmp_dir}/klikkikuri_requests_cache", expire_after=3600)


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


ArticleTitleData = tuple[Article, ArticleTitleResponse]
"""Needed for passing article URLs along with title processing results"""

RahtiEntry = ...
"""Placeholder for future"""


def hash_url(url: str) -> str:
    # TODO: Figure out this Wasm thingy.
    # sign = suola.exports.GetSignature(link)
    # suola = Instantiate("./suola/build/wasi.wasm")
    return hashlib.sha256(bytes(url, encoding="utf-8")).hexdigest()


def fetch_articles() -> list[Article]:
    processor = Iltalehti()
    links = processor.latest()
    pprint(links)

    articles = []
    for url in links:
        article_object = trafilatura_extractor(url)
        articles.append(article_object)
    return articles


def process_titles(articles: list[Article]) -> list[ArticleTitleData]:
    predictor = TitlePredictor()
    results = []
    for article in articles:
        result = predictor.run(article)
        results.append((article, result))
    return results


def convert_for_publish(results: list[ArticleTitleData]) -> list[RahtiEntry]:
    """Convert results into the public Klikkikuri data format"""
    entries = []
    for article, title_obj in results:
        urls = []
        for url in article.urls:
            sign = hash_url(str(url.href))
            urls.append(
                {
                    "labels": [
                        # TODO: Replace this default.
                        "com.github.klikkikuri/link-rel=canonical"
                    ],
                    "sign": sign,
                }
            )
        updated = str(article.updated_at.replace(tzinfo=timezone.utc))
        title = title_obj.title
        clickbaitiness = title_obj.original_title_clickbaitiness

        entries.append(
            {
                "updated": updated,
                "urls": urls,
                "title": title,
                "clickbaitiness": clickbaitiness,
                "labels": [
                    # TODO: Replace this default.
                    "com.github.klikkikuri/article-type=article"
                ],
            }
        )

    return entries


RahtiData = dict


def fetch_old_data() -> tuple[str, RahtiData]:
    old_data_file_obj = requests.get(
        "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json",
        headers={
            "Accept": "application/vnd.github.object",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    ).json()

    old_data = json.loads(base64.b64decode(old_data_file_obj["content"]))

    return old_data_file_obj["sha"], old_data


def filter_outdated(articles: list[Article], old_entries: list[RahtiEntry]) -> list[Article]:
    # When signatures match, use the newer-released article's entry.
    # Initially assume that we are just saving the old article already stored
    # and now pulled from storage for possible update.
    new_articles = []
    old_signatures = [set(map(lambda url: url["sign"], x["urls"])) for x in old_entries]
    new_signatures = [set(map(lambda url: hash_url(str(url.href)), x.urls)) for x in articles]
    pprint(old_signatures)
    pprint(new_signatures)
    for i, news in enumerate(new_signatures):
        for j, olds in enumerate(old_signatures):
            if olds & news:
                # Signature match! Dealing with an article already once processed.
                # NOTE: Assuming the article.updated_at is a naive utc time.
                new_datetime = articles[i].updated_at.replace(tzinfo=timezone.utc)
                old_datetime = datetime.fromisoformat(old_entries[j]["updated"])
                if new_datetime > old_datetime:
                    # The new object has an updated version of the article, so
                    # select that instead.
                    new_articles.append(articles[i])
                break
        else:
            # If this new object has no signatures matching any old one, the
            # entry is totally new.
            new_articles.append(articles[i])
    return new_articles


def merge_updates(old_entries: list[RahtiEntry], new_entries: list[RahtiEntry]) -> list[RahtiEntry]:
    entries = old_entries
    old_signatures = [set(map(lambda url: url["sign"], x["urls"])) for x in old_entries]
    new_signatures = [set(map(lambda url: url["sign"], x["urls"])) for x in new_entries]
    pprint(old_signatures)
    pprint(new_signatures)
    for i, news in enumerate(new_signatures):
        for j, olds in enumerate(old_signatures):
            if olds & news:
                # At this point, the "new" one should be known to be an updated
                # version of a previously processed article, and should thus
                # replace the old version.
                entries[j] = new_entries[i]
                break
        else:
            # If this new object has no signatures matching any old one, the
            # entry is totally new.
            entries.append(new_entries[i])
    return entries


def store_results(hash_of_stored_file: str, entries: list[RahtiEntry]):
    """
    Add the result data of a processing run into the existing Rahti storage.

    NOTE that any matching signatures of entries in existing and new list will
    be replaced by the ones with the latest time stamp.
    """
    updated = str(datetime.now(timezone.utc))

    data = {
        "status": "ok",
        "updated": updated,
        "schema_version": "0.1.0",
        "entries": entries,
    }
    pprint(data)

    # Push the data to Rahti, finishing the processing run.
    encoded_file_content = base64.b64encode(bytes(json.dumps(data, indent=2), encoding="utf-8")).decode("utf-8")
    res = requests.put(
        "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "message": "feat: Add results of newest processing run" if entries else "feat: Empty the list of entries",
            "committer": {
                "name": "Tessa Testaaja",
                "email": "klikkikuri@protonmail.com",
            },
            "content": encoded_file_content,
            "sha": hash_of_stored_file,
        },
    )

    res_json = res.json()

    if not res.ok:
        raise Exception(f"Failed updating rahti: {res.status_code} - {json.dumps(res_json)}")


@cli.command()
@click.argument("article_limit", required=False, type=int)
@click.option("--range-start", help="Start index to the source's list of article's", required=False, type=int)
@click.option(
    "--range-amount",
    help="Amount of items to take from the source's list of article's",
    required=False,
    type=int,
    default=1,
)
def run(article_limit, range_start, range_amount):
    """
    Run the Meri title processing routine once.
    """
    # NOTE: Needed env variables:
    # OPENAI_API_KEY, GITHUB_TOKEN
    articles = fetch_articles()
    if range_start:
        articles = articles[range_start : range_start + range_amount]
    if article_limit:
        articles = articles[:article_limit]

    print("ARTICLES:")
    pprint(articles)
    print("\n")

    hash_of_stored_file, old_data = fetch_old_data()
    old_entries = old_data["entries"]
    pprint(old_entries)
    # Use this for emptying Rahti while developing.
    # store_results(hash_of_stored_file, []); return

    new_articles = filter_outdated(articles, old_entries)
    pprint(new_articles)

    title_data = process_titles(new_articles)
    pprint(title_data)

    new_entries = convert_for_publish(title_data)
    pprint(new_entries)

    entries = merge_updates(old_entries, new_entries)
    pprint(new_entries)

    store_results(hash_of_stored_file, entries)


if __name__ == "__main__":
    cli()
