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
from meri.abc import ArticleTitleResponse
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

ArticleTitleData = tuple[str, ArticleTitleResponse]
"""Means (article_url, obj)"""

RahtiEntry = ...
"""Placeholder for future"""


def convert_for_publish(results: list[ArticleTitleData]) -> list[RahtiEntry]:
    """Convert results into the public Klikkikuri data format"""
    # TODO: Figure out this Wasm mess.
    #suola = Instantiate("./suola/build/wasi.wasm")
    entries = []
    for url, title_obj in results:
        # TODO: Wasm thingy.
        #sign = suola.exports.GetSignature(link)
        sign = hashlib.sha256(bytes(url, encoding="utf-8")).hexdigest()
        # FIXME: This should be the article's publish/edit time, now Meri processing time.
        updated = str(datetime.now(timezone.utc))
        title = title_obj.title
        clickbaitiness = title_obj.original_title_clickbaitiness

        entries.append({
            "updated": updated,
            "urls": [
                # TODO: Gather all article's urls here.
                {
                    "labels": [
                        # TODO: Replace this default.
                        "com.github.klikkikuri/link-rel=canonical"
                    ],
                    "sign": sign,
                }
            ],
            "title": title,
            "clickbaitiness": clickbaitiness,
            "labels": [
                # TODO: Replace this default.
                "com.github.klikkikuri/article-type=article"
            ],
        })

    return entries


def store_results(new_entries: list[RahtiEntry]):
    """
    Add the result data of a processing run into the existing Rahti storage.

    NOTE that any matching signatures of entries in existing and new list will
    be replaced by the ones with the latest time stamp.
    """
    old_data_file_obj = requests.get(
        "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json",
        headers={
            "Accept": "application/vnd.github.object",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    ).json()

    old_data = json.loads(base64.b64decode(old_data_file_obj["content"]))
    pprint(old_data)

    old_entries = old_data["entries"]
    # When signatures match, use the newer-released article's entry.
    # TODO: Do this filtering __BEFORE__ passing to title prediction.
    # Initially assume that we are just saving the old article already stored
    # and now pulled from storage for possible update.
    entries = old_entries
    old_signatures = [set(map(lambda url: url["sign"], x["urls"])) for x in old_entries]
    new_signatures = [set(map(lambda url: url["sign"], x["urls"])) for x in new_entries]
    pprint(old_signatures)
    pprint(new_signatures)
    for i, news in enumerate(new_signatures):
        for j, olds in enumerate(old_signatures):
            if olds & news:
                # Signature match! Dealing with an article already once processed.
                if datetime.fromisoformat(new_entries[i]["updated"]) > datetime.fromisoformat(old_entries[j]["updated"]):
                    # The new object has an updated version of the article, so
                    # select that instead.
                    entries[j] = new_entries[i]
                break
        else:
            # If this new object has no signatures matching any old one, the
            # entry is totally new.
            entries.append(new_entries[i])
    pprint(entries)

    updated = str(datetime.now(timezone.utc))
    data = {
        "status": "ok",
        "updated": updated,
        "schema_version": "0.1.0",
        "entries": entries
    }
    pprint(data)

    # Push the data to Rahti, finishing the processing run.
    encoded_file_content = base64.b64encode(bytes(json.dumps(data, indent=2), encoding="utf-8")).decode("utf-8")
    file_hash = old_data_file_obj["sha"]
    res = requests.put(
        "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "message": "feat: Add results of newest processing run",
            "committer":
                {
                    "name": "Tessa Testaaja",
                    "email": "klikkikuri@protonmail.com",
                },
            "content": encoded_file_content,
            "sha": file_hash,
        }
    )

    res_json = res.json()

    if not res.ok:
        raise Exception(f"Failed updating rahti: {res.status_code} - {json.dumps(res_json)}")


@cli.command()
@click.argument("url", required=False, type=AnyUrl)
@tracer.start_as_current_span("fetch")
def fetch(url=None):
    """
    Fetch article from URL.
    """
    from .extractor.iltalehti import Iltalehti
    from .pipelines.title import TitlePredictor
    from meri.extractor._extractors import trafilatura_extractor

    # NOTE: Needed env variables:
    # OPENAI_API_KEY, GITHUB_USER, GITHUB_PASSWORD
    processor = Iltalehti()
    links = processor.latest()
    pprint(links)

    # TODO: Run for all links.
    results = []
    for url in links[:5]:
        article_object = trafilatura_extractor(url)
        pprint(article_object)
        predictor = TitlePredictor()
        result = predictor.run(article_object)
        results.append((url, result))
    pprint(results)

    new_entries = convert_for_publish(results)
    pprint(new_entries)
    store_results(new_entries)


if __name__ == "__main__":
    cli()
