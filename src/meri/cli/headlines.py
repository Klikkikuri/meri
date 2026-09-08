"""
Testing command that lists the headlines the configured sources would feed into a run.

It stops at discovery: nothing is extracted, generated or written to Rahti, so it is a cheap way to see what a
`meri run` would pick up, or to grab a few real URLs for `meri test`.
"""

import random
from datetime import UTC, datetime

from ..article import Article
from ..lautta import DiscoveredArticle, fetch_latest
from ..settings.settings import Settings

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

_UNDATED = datetime.min.replace(tzinfo=UTC)


def _created_at(discovered: DiscoveredArticle) -> datetime:
    """Sort key that puts undated articles last, since the feed did not say how new they are."""
    return discovered.article.created_at or _UNDATED


def select(discovered: list[DiscoveredArticle], limit: int | None, sample: bool) -> list[DiscoveredArticle]:
    """
    Pick the articles to show: the newest, or a random sample when `sample` is set.

    :param discovered: Everything the sources returned.
    :param limit: How many to keep, or None for all.
    :param sample: Pick at random instead of by age.
    """
    if sample:
        count = len(discovered) if limit is None else min(limit, len(discovered))
        return random.sample(discovered, count)
    newest = sorted(discovered, key=_created_at, reverse=True)
    return newest if limit is None else newest[:limit]


def format_line(discovered: DiscoveredArticle) -> str:
    """One headline as `timestamp  source  title  url`, with placeholders where the feed gave nothing."""
    article: Article = discovered.article
    stamp = article.created_at.isoformat(timespec="minutes") if article.created_at else "-"
    return f"{stamp}\t{discovered.source.name or '-'}\t{article.title or '-'}\t{article.get_url() or '-'}"


@click.command("headlines")
@click.option("--limit", type=click.IntRange(min=1), help="Show at most this many headlines. Defaults to all.")
@click.option("--sample", is_flag=True, help="Pick headlines at random instead of the newest ones.")
@click.pass_context
def cli(ctx: click.Context, limit: int | None, sample: bool) -> None:
    """
    Print the latest headlines from the configured sources, newest first.

    A testing aid: it only discovers, so no article is extracted or sent to a model, and Rahti is not touched.
    """
    settings: Settings = ctx.obj["settings"]
    for discovered in select(fetch_latest(settings.sources), limit, sample):
        click.echo(format_line(discovered))
