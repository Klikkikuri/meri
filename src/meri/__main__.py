import base64
from datetime import datetime, timezone
import hashlib
import os
import json
from textwrap import wrap

import dotenv
from pydantic import AnyUrl
from rich.pretty import pprint  # TODO: remove from production code
import requests

from meri.abc import ArticleTitleResponse, Article
from .utils import setup_logging
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
logger = get_logger(__package__)


@click.group()
@click.version_option()
@click.option("--cache/--no-cache", help="Enable or disable requests cache.", default=os.getenv("REQUESTS_CACHE", True))
def cli(cache: bool):
    if cache:
        import requests_cache
        import tempfile

        # get temp directory
        tmp_dir = tempfile.gettempdir()
        requests_cache.install_cache(f"{tmp_dir}/klikkikuri_requests_cache", expire_after=3600)


@cli.command()
@click.argument("url", required=False, type=AnyUrl)
def fetch(url=None):
    """
    Fetch article from URL.
    """
    if not url:
        url = "https://www.iltalehti.fi/"
        logger.info("Pulling latest from %r", url)
        source = extractor(url)
        latest = source.latest()
        logger.debug("Retrieved %d latest articles", len(latest), latest=latest)

        url = latest[0]

    url = str(url)
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
    url = str(url)
    # TODO: Figure out this Wasm thingy.
    # sign = suola.exports.GetSignature(link)
    # suola = Instantiate("./suola/build/wasi.wasm")
    return hashlib.sha256(bytes(url, encoding="utf-8")).hexdigest()


def fetch_latest() -> list[Article]:
    processor = Iltalehti()
    links = processor.latest()
    pprint(links)

    return links


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
        updated = str(article.updated_at.astimezone(timezone.utc))
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
        timeout=30
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
            if olds == news:
                # Signature match! Dealing with an article already once processed.
                new_datetime = articles[i].updated_at
                old_datetime = datetime.fromisoformat(old_entries[j]["updated"])
                if new_datetime > old_datetime:
                    logger.info("Article updated, replacing old entry", url=articles[i].urls, new=new_datetime, old=old_datetime)
                    # The new object has an updated version of the article, so
                    # select that instead.
                    new_articles.append(articles[i])
                break
        else:
            # If this new object has no signatures matching any old one, the
            # entry is totally new.
            new_articles.append(articles[i])
    return new_articles


def fetch_articles(article_stubs: list[Article]) -> list[Article]:
    """
    Fetch full articles from the given article stubs.

    Returned articles have metadata merged from the stubs.
    """

    articles = []

    for stub in article_stubs:
        if not (url := str(stub.get_url())):
            raise ValueError(f"Article stub has no URL: {stub}")
        
        article_object = trafilatura_extractor(url)

        # Merge the stub and the fetched article object.
        # Use the latest updated_at and earliest created_at.
        article_object.updated_at = max(stub.updated_at, article_object.updated_at)
        article_object.created_at = min(stub.created_at, article_object.created_at)
        # Merge missing metadata from stub to fetched object.
        for k, v in stub.meta.items():
            if k not in article_object.meta or not article_object.meta[k]:
                article_object.meta[k] = v
        # Append missing URLs from stub to fetched object.
        existing_urls = set(map(lambda u: str(u.href), article_object.urls))
        for url in stub.urls:
            if str(url.href) not in existing_urls:
                article_object.urls.append(url)

        articles.append(article_object)

    return articles



def prune_old_entries(old_entries: list[RahtiEntry], max_age_days: int = 3) -> list[RahtiEntry]:
    """
    Remove entries older than max_age_days from the list of old entries.
    """
    pruned = []
    now = datetime.now(timezone.utc)
    for entry in old_entries:
        entry_time = datetime.fromisoformat(entry["updated"])
        age_days = (now - entry_time).days
        if age_days <= max_age_days:
            pruned.append(entry)
        else:
            logger.debug("Pruning old entry", title=entry["title"], age_days=age_days)
    return pruned


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


def store_results(hash_of_stored_file: str, entries: list[RahtiEntry], commit_message: str):
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
            "message": commit_message,
            "committer": {
                "name": "[🤖 bot] Klikkikuri harbormaster",
                "email": "klikkikuri+satamamestari@protonmail.com",
            },
            "content": encoded_file_content,
            "sha": hash_of_stored_file,
        },
        timeout=30
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
    articles = fetch_latest()

    commit_message = []

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

    new_articles = fetch_articles(new_articles)

    # remove old entries before merging in new ones.    
    original_count = len(old_entries)
    old_entries = prune_old_entries(old_entries)
    pruned_count = len(old_entries)

    title_data = process_titles(new_articles)

    new_entries = convert_for_publish(title_data)

    entries = merge_updates(old_entries, new_entries)
    
    commit_message.append(f"[🤖 bot]: Updated list with {len(new_entries)} additions or updates, and removed {original_count - pruned_count} old entries.")
    if len(title_data) > 0:
        commit_message.append("")
        commit_message.append("New or updated entries:")
        

        for i, (a, t) in enumerate(title_data):
            a: Article
            t: ArticleTitleResponse
            sign = new_entries[i]["urls"][0]["sign"]
            commit_message.append("")
            commit_message.append(f" - {sign}:")
            commit_message += wrap(t.contemplator, initial_indent="   ", subsequent_indent="   ", break_long_words=False, width=72)

    store_results(hash_of_stored_file, entries, "\n".join(commit_message))


if __name__ == "__main__":
    cli()
