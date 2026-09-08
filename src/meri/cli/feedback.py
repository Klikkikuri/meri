"""
Maintainer commands for the reader feedback guardrails.

These read Meri's configuration, so an operator names paths once, in `config.yaml`, instead of repeating them
on a command line. Guard training is Luotsi's work; translation is Meri's, through the `feedback_translate`
pipeline, because Luotsi carries no LLM client. None of this runs in a deployment.

The injection guard's centroid artifact is deliberately NOT shipped with Luotsi: it is bound to the embedding
model it was trained with, so each deployment trains its own from the exemplar data.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from luotsi.embeddings import download_model, load_embedder
from luotsi.guards.labeled import (
    LabeledLine,
    packaged_exemplars,
    parse,
    parse_files,
    write,
)
from luotsi.guards.trainer import DEFAULT_CLUSTER_THRESHOLD, train_centroids
from luotsi.settings import InjectionConfig, LuotsiSettings
from niitti import get_logger

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

if TYPE_CHECKING:
    from click import Command as CommandBase
else:
    # `cli.command()` builds RichCommands when rich_click is installed; subclass whichever is in play, so the
    # one command with a custom help still renders like every other.
    CommandBase = getattr(click, "RichCommand", click.Command)

from ..settings.luotsi import DEFAULT_EMBEDDING_MODEL, hub_id_of, model_dir
from ..settings.settings import Settings

logger = get_logger(__name__)


def _luotsi_settings(ctx: click.Context) -> LuotsiSettings:
    """Read the `luotsi` section, or fail with the reason it is needed."""
    settings: Settings = ctx.obj["settings"]
    if not settings.luotsi:
        raise click.ClickException("No `luotsi:` section in the configuration. See config.example.yaml.")
    return settings.luotsi


def _embedding_model(settings: LuotsiSettings) -> Path:
    """Read the directory the configured model loads from, or say how to get one."""
    if not settings.embedding_model:
        raise click.ClickException(
            "`luotsi.embedding_model` is null, so no model is configured. Name one, then provision it with "
            "`meri feedback download-model`."
        )
    return settings.embedding_model


def _configured_model(ctx: click.Context) -> Path | None:
    """
    The directory the configured model loads from, or None.

    Settings hold it already resolved, hub identifier and all. Unlike `_embedding_model` this never raises: it
    also runs while formatting `--help`, where the command has nothing to fail.
    """
    settings: Settings | None = (ctx.obj or {}).get("settings")
    return settings.luotsi.embedding_model if settings and settings.luotsi else None


def _download_defaults(ctx: click.Context, target_dir: Path | None, model: str | None) -> tuple[Path, str]:
    """
    Resolve what `download-model` fetches and where it saves it.

    The configured `luotsi.embedding_model` drives both, so provisioning a deployment repeats nothing from
    `config.yaml`: it names the directory to fill, and the hub identifier it was resolved from is the model to
    fetch into it.

    :param ctx: Command context, for the settings.
    :param target_dir: TARGET_DIR as given, or None.
    :param model: `--model` as given, or None.
    :return: The destination directory and the hub identifier to fetch.
    """
    destination = target_dir or _configured_model(ctx) or model_dir(DEFAULT_EMBEDDING_MODEL)
    return destination, model or hub_id_of(destination) or DEFAULT_EMBEDDING_MODEL


class ShowsResolvedDefaults(CommandBase):
    """
    A command whose help names the model it would fetch and the directory it would save it in.

    TARGET_DIR is a click argument and `--model` defaults to a configured value, and neither reaches
    `show_default`, so both resolved defaults are written into the epilog — an operator should not have to read
    config.yaml to learn what this would do.
    """

    def get_help(self, ctx: click.Context) -> str:
        destination, model = _download_defaults(ctx, None, None)
        self.epilog = f"Without arguments it fetches `{model}` into {destination}"
        return super().get_help(ctx)


def _vectors_path(settings: LuotsiSettings) -> Path | None:
    """The artifact path the configured injection guard reads, when one is configured."""
    for guard in settings.guardrails or []:
        if isinstance(guard, InjectionConfig) and guard.vectors:
            return guard.vectors
    return None


@click.group("feedback")
def cli() -> None:
    """Reader feedback tooling: provision the embedding model and train the injection guard."""


@cli.command("download-model", cls=ShowsResolvedDefaults)
@click.argument("target_dir", type=click.Path(path_type=Path), required=False)
@click.option(
    "--model",
    help="Model2Vec model to fetch from the hub. Defaults to the hub model `luotsi.embedding_model` names, "
    f"otherwise to {DEFAULT_EMBEDDING_MODEL}.",
)
@click.pass_context
def download(ctx: click.Context, target_dir: Path | None, model: str | None) -> None:
    """
    Fetch a Model2Vec model into TARGET_DIR.

    Both arguments default to the configured `luotsi.embedding_model`: it names the model to fetch, and the
    directory it resolves to is where the runs then load it from. Neither has to exist yet, so provisioning a
    configured deployment is this command with no arguments at all. Give TARGET_DIR only to save somewhere the
    configuration does not name; the command then prints the configuration line to add.
    """
    destination, model = _download_defaults(ctx, target_dir, model)

    click.echo(f"Downloading {model} into {destination} ...")
    try:
        download_model(destination, model)
    except ImportError as e:
        raise click.ClickException(str(e)) from e

    if destination == _configured_model(ctx):
        click.echo(f"Saved to {destination}, which is where `luotsi.embedding_model` loads from.")
    else:
        click.echo(
            f"Saved to {destination}. Add to your configuration:\n\n  luotsi:\n    embedding_model: "
            f"{hub_id_of(destination) or destination.resolve()}\n"
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
    model_path = _embedding_model(settings)

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
    click.echo(f"Training on {len(exemplars)} exemplar(s) with {model_path.name} ...")

    artifact, report = train_centroids(
        exemplars,
        load_embedder(model_path),
        model_name=model_path.name,
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
@click.pass_context
def translate_exemplars(ctx: click.Context, src: Path, dst: Path, language: str) -> None:
    """
    Translate a labeled exemplar file into another language.

    Runs through the `feedback_translate` pipeline, so the model and its credentials come from Meri's `llm:` and
    `pipelines:` configuration like any other pipeline. Read the result before committing it.
    """
    # Imported here, and constructed inside the command, because `settings` only resolves in a setup() context.
    from ..pipelines.feedback_translate import ExemplarTranslator

    exemplars = parse(src.read_text(encoding="utf-8").splitlines())
    click.echo(f"Translating {len(exemplars)} exemplar(s) to {language} ...")

    # The pipeline takes and returns plain text. Labels are this file's format, so they are split off here and
    # re-attached from the source, never read back from the model.
    try:
        translated = ExemplarTranslator().translate([(line.label, line.text) for line in exemplars], language)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    result = [LabeledLine(source.label, text) for source, text in zip(exemplars, translated, strict=True)]
    dst.write_text(write(result), encoding="utf-8")
    click.echo(f"Wrote {len(result)} exemplar(s) to {dst}. Read them before committing.")
