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
from .guards.trainer import DEFAULT_CLUSTER_THRESHOLD

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
"""101 languages in one 256-dimension vector space, so Finnish and English feedback share a metric."""

DEFAULT_TRANSLATE_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_TRANSLATE_MODEL = "gpt-4o-mini"


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


@cli.command("translate-exemplars")
@click.argument("src", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("dst", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--to", "language", required=True, help="Target language, named as you would to a translator.")
@click.option("--endpoint", default=DEFAULT_TRANSLATE_ENDPOINT, show_default=True, help="Chat completions URL.")
@click.option("--model", default=DEFAULT_TRANSLATE_MODEL, show_default=True, help="Model to translate with.")
def translate_exemplars(src: Path, dst: Path, language: str, endpoint: str, model: str) -> None:
    """
    Translate a labeled exemplar file into another language.

    A maintainer tool. Skim the output before committing it, and never run it in a deployment — the data files
    ship translated. Reads the API key from `LUOTSI_TRANSLATE_API_KEY`, or `OPENAI_API_KEY`.
    """
    from .guards.labeled import parse, write
    from .guards.translate import translate

    exemplars = parse(src.read_text(encoding="utf-8").splitlines())
    click.echo(f"Translating {len(exemplars)} exemplar(s) to {language} with {model} ...")

    try:
        translated = translate(exemplars, language, endpoint=endpoint, model=model)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    dst.write_text(write(translated), encoding="utf-8")
    click.echo(f"Wrote {len(translated)} exemplar(s) to {dst}. Read them before committing.")


@cli.command("train-guard")
@click.argument("output", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--data",
    "data_files",
    multiple=True,
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Labeled exemplar file. Repeat to train every language into one artifact.",
)
@click.option(
    "--model",
    "model_dir",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Local Model2Vec model directory.",
)
@click.option(
    "--cluster-threshold",
    default=DEFAULT_CLUSTER_THRESHOLD,
    show_default=True,
    help="Similarity at which two exemplars of one label share a centroid.",
)
@click.option("--floor", default=0.60, show_default=True, help="Decision floor the self-check reports against.")
@click.option("--margin", default=0.10, show_default=True, help="Decision margin the self-check reports against.")
def train_guard(
    output: Path, data_files: tuple[Path, ...], model_dir: Path, cluster_threshold: float, floor: float, margin: float
) -> None:
    """
    Train the injection guard's centroid artifact into OUTPUT.

    Deterministic: re-run it whenever the exemplar files change or a different embedding model is adopted.
    """
    from .embeddings import load_embedder
    from .guards.labeled import parse_files
    from .guards.trainer import train_centroids

    exemplars = parse_files(data_files)
    embed = load_embedder(model_dir)

    artifact, report = train_centroids(
        exemplars, embed, model_name=model_dir.name, cluster_threshold=cluster_threshold, floor=floor, margin=margin
    )
    output.write_text(artifact.dump(), encoding="utf-8")

    click.echo(f"\nWrote {len(artifact.centroids)} centroid(s) to {output}\n")
    click.echo(report.render())


if __name__ == "__main__":
    sys.exit(cli())
