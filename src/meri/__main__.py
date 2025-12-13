import base64
from datetime import datetime, timezone
import hashlib
from importlib.util import find_spec
import os
import json
from textwrap import wrap

import dotenv
from pydantic import AnyHttpUrl
from rich.pretty import pprint  # TODO: remove from production code
import requests

from meri.abc import ArticleTitleResponse
from meri.article import Article
from .utils import setup_logging
from structlog import get_logger
from .scraper import get_extractor, try_setup_requests_cache, discover_articles
from .pipelines.title import TitlePredictor
from meri.extractor._extractors import trafilatura_extractor
from meri.extractor import get_default_extractors
from meri.discovery import merge_article_lists

from meri.settings import settings

try:
    import rich_click as click
except ImportError:
    import click

dotenv.load_dotenv()

setup_logging()
logger = get_logger(__package__)

# Check if requests_cache is available, since it is not a hard dependency and not installed by default
_requests_cache_available: bool = find_spec("requests_cache") is not None

extractors = {e.name: e for e in get_default_extractors()}


@click.group()
@click.version_option()
@click.option("--cache/--no-cache", help="Enable or disable requests cache.", default=bool(os.getenv("REQUESTS_CACHE", _requests_cache_available)))

def cli(cache: bool):

    if cache:
        if not _requests_cache_available:
            raise RuntimeError("requests_cache is not available, cannot enable caching.")

        try_setup_requests_cache()

    os.environ["REQUESTS_CACHE"] = "1" if cache else "0"


@cli.command()
@click.option("--extractor", required=False, type=click.Choice(list(extractors.keys()), case_sensitive=False))  # type: ignore
@click.argument("url", required=True, type=AnyHttpUrl)
def fetch(url: AnyHttpUrl, extractor=None):
    """
    Fetch article from URL.
    """

    if not extractor:
        outlet = get_extractor(url)
        if not outlet:
            raise ValueError(f"No extractor found for URL {url}")
    else:
        outlet = extractors.get(extractor)
        if not outlet:
            raise ValueError(f"No extractor found with name {extractor}")


    article = outlet.fetch(url)
    if getattr(article, "html", None):
        article.html = article.html[:1000] + "..." if article.html and len(article.html) > 1000 else article.html

    pprint(article.dict())


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
    """
    Fetch latest articles from all configured news sources.
    
    Returns a deduplicated list of articles from all enabled sources.
    """
    
    article_lists: list[list[Article]] = []
    
    for source in settings.sources:
        if not source.enabled:
            logger.debug("Skipping disabled source", source=source.name)
            continue
        
        try:
            logger.info("Fetching articles from source", source=source.name, type=source.type)
            articles = discover_articles(source)
            article_lists.append(articles)
            logger.info("Fetched %d articles from %s", len(articles), source.name)
        except Exception as e:
            logger.error(
                "Failed to fetch articles from source",
                source=source.name,
                error=str(e),
                exc_info=True
            )
            continue
    
    # Merge all article lists and remove duplicates across sources
    all_articles = merge_article_lists(*article_lists)
    logger.info(
        "Fetched total of %d unique articles from %d sources",
        len(all_articles),
        len([s for s in settings.sources if s.enabled])
    )
    
    return all_articles


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
        article_object.merge(stub)

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


@cli.command()
def list_sources():
    """
    List available extractors.
    """
    import meri.settings

    for source in meri.settings.settings.sources:
        print(f"Extractor: {source.name} (weight={source})")


@cli.command()
def test_fetch():
    """
    Test fetching latest news from all configured sources.
    
    This command fetches articles from all enabled news sources and displays
    a summary. Use this to verify your source configuration is working correctly.
    """
    from meri.settings import settings
    
    if not settings.sources:
        click.echo("No news sources configured. Add sources to your config.yaml file.")
        return
    
    click.echo(f"Fetching articles from {len(settings.sources)} configured source(s)...\n")
    
    articles = fetch_latest()
    
    if not articles:
        click.echo("No articles found.")
        return
    
    click.echo(f"\n{'='*80}")
    click.echo(f"Found {len(articles)} unique article(s)")
    click.echo(f"{'='*80}\n")
    
    limit = 10
    show_meta = True
    
    display_articles = articles[:limit] if limit else articles
    
    for i, article in enumerate(display_articles, 1):
        url = article.get_url()
        title = article.meta.get("title", "No title")
        published = article.created_at
        
        click.echo(f"{i}. {title}")
        click.echo(f"   URL: {url}")
        click.echo(f"   Updated: {published}")
        
        if show_meta:
            click.echo(f"   Metadata: {article.meta}")

        click.echo()
    
    if limit and len(articles) > limit:
        click.echo(f"... and {len(articles) - limit} more article(s)")


if __name__ == "__main__":
    cli()
