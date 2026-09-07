"""
Luotsi command line.

Maintainer tools. None of these run in a deployment: they provision the embedding model, grow the labeled
exemplar data, and train the injection guard's artifact — all offline, with the results committed.
"""

import logging
import sys
from pathlib import Path

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

from .embeddings import INSTALL_HINT

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
"""101 languages in one 256-dimension vector space, so Finnish and English feedback share a metric."""


@click.group()
@click.version_option()
def cli() -> None:
    """Luotsi 🧭 — reader feedback tooling."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


@cli.command("download-model")
@click.argument("target_dir", type=click.Path(path_type=Path))
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Model2Vec model to fetch from the hub.")
def download_model(target_dir: Path, model: str) -> None:
    """
    Fetch a Model2Vec model into TARGET_DIR.

    This is the sanctioned way to provision the model, so that the runtime container needs no hub access.
    """
    try:
        from model2vec import StaticModel
    except ImportError:
        raise click.ClickException(INSTALL_HINT) from None

    click.echo(f"Downloading {model} ...")
    StaticModel.from_pretrained(model).save_pretrained(str(target_dir))

    click.echo(f"Saved to {target_dir}. Add to your configuration:\n\n  embedding_model: {target_dir.resolve()}\n")


if __name__ == "__main__":
    sys.exit(cli())
