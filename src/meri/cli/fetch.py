"""
Testing command that fetches one article and prints it as Markdown.

It runs the same extractor `meri run` would pick for the URL, and stops there: no model is called and Rahti is
not touched. Use it to check what an extractor pulls out of a page.
"""

from ..article import Article
from ..scraper import get_extractor

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]


def render(article: Article) -> str:
    """
    Lay the article out as a Markdown document: title, a metadata list, then the extracted text.

    The text is already Markdown, the extractors convert it on the way in, so it is written out as is.
    """
    meta = article.meta
    facts = [
        ("URL", article.get_url()),
        ("Outlet", meta.get("outlet")),
        ("Authors", ", ".join(meta.get("authors") or [])),
        ("Language", meta.get("language")),
        ("Created", article.created_at),
        ("Updated", article.updated_at),
        ("Labels", ", ".join(str(label.value) for label in article.labels)),
    ]
    lines = [f"# {article.title or '(untitled)'}", ""]
    lines.extend(f"- **{name}:** {value}" for name, value in facts if value)
    lines.extend(["", article.text or "*(no text extracted)*", ""])
    return "\n".join(lines)


@click.command("fetch")
@click.argument("url")
def cli(url: str) -> None:
    """
    Fetch one article with its extractor and print it as Markdown.

    A testing aid: it extracts only, so nothing is sent to a model and Rahti is not touched.
    """
    click.echo(render(get_extractor(url).fetch_by_url(url)), nl=False)
