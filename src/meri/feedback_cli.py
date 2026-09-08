"""
Maintainer commands for the reader feedback guardrails.

These read Meri's configuration and hand the work to Luotsi, so an operator names paths once, in `config.yaml`,
instead of repeating them on a command line. None of this runs in a deployment.

The injection guard's centroid artifact is deliberately NOT shipped with Luotsi: it is bound to the embedding
model it was trained with, so each deployment trains its own from the exemplar data.
"""

from pathlib import Path

from luotsi.embeddings import DEFAULT_MODEL, download_model, load_embedder
from luotsi.guards.labeled import packaged_exemplars, parse, parse_files, write
from luotsi.guards.trainer import DEFAULT_CLUSTER_THRESHOLD, train_centroids
from luotsi.guards.translate import DEFAULT_ENDPOINT, translate
from luotsi.guards.translate import DEFAULT_MODEL as DEFAULT_TRANSLATE_MODEL
from luotsi.settings import InjectionConfig, LuotsiSettings
from niitti import get_logger

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

from .settings.settings import Settings

logger = get_logger(__name__)


def _luotsi_settings(ctx: click.Context) -> LuotsiSettings:
    """Read the `luotsi` section, or fail with the reason it is needed."""
    settings: Settings = ctx.obj["settings"]
    if not settings.luotsi:
        raise click.ClickException("No `luotsi:` section in the configuration. See config.example.yaml.")
    return settings.luotsi


def _embedding_model(settings: LuotsiSettings) -> Path:
    """Read the configured embedding model directory, or say how to provision one."""
    if not settings.embedding_model:
        raise click.ClickException(
            "No `luotsi.embedding_model` configured. Provision one with `meri feedback download-model`."
        )
    return settings.embedding_model


def _vectors_path(settings: LuotsiSettings) -> Path | None:
    """The artifact path the configured injection guard reads, when one is configured."""
    for guard in settings.guardrails or []:
        if isinstance(guard, InjectionConfig) and guard.vectors:
            return guard.vectors
    return None


@click.group("feedback")
def cli() -> None:
    """Reader feedback tooling: provision the embedding model and train the injection guard."""


@cli.command("download-model")
@click.argument("target_dir", type=click.Path(path_type=Path))
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Model2Vec model to fetch from the hub.")
def download(target_dir: Path, model: str) -> None:
    """
    Fetch a Model2Vec model into TARGET_DIR.

    TARGET_DIR is an argument rather than a setting because this runs BEFORE the model can be configured. It
    prints the configuration line to add once the download finishes.
    """
    click.echo(f"Downloading {model} ...")
    try:
        download_model(target_dir, model)
    except ImportError as e:
        raise click.ClickException(str(e)) from e

    click.echo(
        f"Saved to {target_dir}. Add to your configuration:\n\n  luotsi:\n    embedding_model: {target_dir.resolve()}\n"
    )


@cli.command("train-guard")
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the artifact. Defaults to the configured injection guard's `vectors` path.",
)
@click.option(
    "--data",
    "data_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Labeled exemplar file. Repeat to add languages. Defaults to the files shipped with Luotsi.",
)
@click.option(
    "--cluster-threshold",
    default=DEFAULT_CLUSTER_THRESHOLD,
    show_default=True,
    help="Similarity at which two exemplars of one label share a centroid.",
)
@click.pass_context
def train_guard(
    ctx: click.Context, output: Path | None, data_files: tuple[Path, ...], cluster_threshold: float
) -> None:
    """
    Train the injection guard's centroid artifact.

    Deterministic. Re-run it whenever the exemplar data changes or a different embedding model is adopted; the
    guard refuses to load an artifact trained at a different dimension.
    """
    settings = _luotsi_settings(ctx)
    model_dir = _embedding_model(settings)

    destination = output or _vectors_path(settings)
    if not destination:
        raise click.ClickException(
            "Nowhere to write the artifact. Set `vectors` on the injection guard in your `luotsi.guardrails` "
            "list, or pass --output."
        )

    injection = next(
        (guard for guard in settings.guardrails or [] if isinstance(guard, InjectionConfig)), InjectionConfig()
    )
    exemplars = parse_files(list(data_files) or packaged_exemplars())
    click.echo(f"Training on {len(exemplars)} exemplar(s) with {model_dir.name} ...")

    artifact, report = train_centroids(
        exemplars,
        load_embedder(model_dir),
        model_name=model_dir.name,
        cluster_threshold=cluster_threshold,
        floor=injection.floor,
        margin=injection.margin,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(artifact.dump(), encoding="utf-8")

    click.echo(f"\nWrote {len(artifact.centroids)} centroid(s) to {destination}\n")
    click.echo(report.render())

    if report.misclassifications:
        click.echo("\nThe self-check found misclassifications. Read them before deploying this artifact.")


@cli.command("translate-exemplars")
@click.argument("src", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("dst", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--to", "language", required=True, help="Target language, named as you would to a translator.")
@click.option("--endpoint", default=DEFAULT_ENDPOINT, show_default=True, help="Chat completions URL.")
@click.option("--model", default=DEFAULT_TRANSLATE_MODEL, show_default=True, help="Model to translate with.")
def translate_exemplars(src: Path, dst: Path, language: str, endpoint: str, model: str) -> None:
    """
    Translate a labeled exemplar file into another language.

    Read the result before committing it. The API key comes from `LUOTSI_TRANSLATE_API_KEY`, or `OPENAI_API_KEY`.
    """
    exemplars = parse(src.read_text(encoding="utf-8").splitlines())
    click.echo(f"Translating {len(exemplars)} exemplar(s) to {language} with {model} ...")

    try:
        translated = translate(exemplars, language, endpoint=endpoint, model=model)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    dst.write_text(write(translated), encoding="utf-8")
    click.echo(f"Wrote {len(translated)} exemplar(s) to {dst}. Read them before committing.")
