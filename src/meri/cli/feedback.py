"""
Maintainer commands for the reader feedback guardrails.

These read Meri's configuration, so an operator names paths once, in `config.yaml`, instead of repeating them
on a command line. Guard training is Luotsi's work; translation is Meri's, through the `feedback_translate`
pipeline, because Luotsi carries no LLM client. None of this runs in a deployment.

The injection guard's centroid artifact is deliberately NOT shipped with Luotsi: it is bound to the embedding
model it was trained with, so each deployment trains its own from the exemplar data.
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from niitti import get_logger

from meri.luotsi.client import model_identity
from meri.luotsi.cluster import MessageClusterer
from meri.luotsi.embeddings import download_model, load_embedder, model_exists
from meri.luotsi.guards.evaluate import embed_lines, evaluate, explain, sweep
from meri.luotsi.guards.labeled import (
    LabeledLine,
    parse,
    parse_files,
    write,
)
from meri.luotsi.guards.provision import ensure_guard_vectors
from meri.luotsi.guards.trainer import training_report
from meri.luotsi.guards.vectors import GuardVectors
from meri.luotsi.settings import InjectionConfig, LuotsiSettings

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

if TYPE_CHECKING:
    from click import Command as CommandBase

    from meri.luotsi.embeddings import Embedder
else:
    # `cli.command()` builds RichCommands when rich_click is installed; subclass whichever is in play, so the
    # one command with a custom help still renders like every other.
    CommandBase = getattr(click, "RichCommand", click.Command)

from ..settings.luotsi import (
    DEFAULT_EMBEDDING_MODEL,
    default_vectors,
    hub_id_of,
    model_dir,
)
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


def _embedder(path: Path) -> "Embedder":
    """Load the configured model, or say how to provision it. These commands read; they never fetch."""
    try:
        return load_embedder(path)
    except FileNotFoundError as e:
        raise click.ClickException(
            f"{e}\n\n`meri feedback download-model` fetches it, and `meri run` does the same at start."
        ) from e


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


def ensure_model(path: Path) -> bool:
    """
    Fetch the configured model when its directory holds none. WRITES, and reaches the hub.

    What `meri run` calls so that a cold deployment provisions itself, the way it already trains its own guard
    vectors. The hub identifier is the one the directory resolves from, so an operator names a model once and a
    run needs nothing else.

    Publishes by rename, for the same reason :func:`~meri.luotsi.guards.provision.write_artifact` does: runs overlap,
    and a second process must never read a half-written model.

    :param path: The resolved model directory, from `luotsi.embedding_model`.
    :raises click.ClickException: When the directory names no hub model, or holds a partial one.
    :return: True when it fetched a model, False when one was already there.
    """
    if model_exists(path):
        return False

    hub_id = hub_id_of(path)
    if not hub_id or hub_id.count("/") != 1:
        raise click.ClickException(
            f"No model in {path}, and nothing says where to fetch one: it is not a directory a hub identifier "
            f"resolves to. Provision it with `meri feedback download-model {path}`, or name a hub model in "
            f"`luotsi.embedding_model`."
        )

    if path.exists() and any(path.iterdir()):
        # An interrupted fetch, so this is not a directory to replace unasked; `download-model` writes in place
        # and is the deliberate way to say "overwrite whatever is there".
        raise click.ClickException(
            f"{path} holds files but no loadable model, which is what an interrupted download leaves. "
            f"`meri feedback download-model` fetches over it."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=path.parent, prefix=f".{path.name}."))
    try:
        download_model(staging, hub_id)
        os.replace(staging, path)
    except OSError:
        # Two runs starting together fetch the same model into staging directories of their own; the loser
        # renames onto a directory that is no longer empty. Its work is redundant, not failed.
        if not model_exists(path):
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return True


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


def _injection_config(settings: LuotsiSettings) -> InjectionConfig:
    """
    The configured injection guard.

    Falls back to the defaults when the chain does not list one, so the maintainer commands work against a
    deployment that has not configured the guard yet — the thresholds are the same either way.
    """
    return next(
        (guard for guard in settings.guardrails or [] if isinstance(guard, InjectionConfig)), InjectionConfig()
    )


def _vectors_path(settings: LuotsiSettings) -> Path:
    """
    The artifact path the deployment uses.

    Mirrors the guard's own resolution — the injection guard's `vectors`, else the destination the settings
    resolved — so these commands read and write exactly the file a run would.
    """
    return _injection_config(settings).vectors or settings.guard_vectors or default_vectors()


class _Guard(NamedTuple):
    """Everything the read-only guard commands need, resolved and loaded."""

    embed: "Embedder"
    artifact: GuardVectors
    floor: float
    margin: float
    path: Path


def _load_guard(ctx: click.Context, vectors: Path | None, floor: float | None, margin: float | None) -> _Guard:
    """
    Load the configured model and artifact, with the thresholds the deployment runs at.

    Shared by `probe` and `check`, which differ only in what they classify. Loading the artifact against the
    live model's dimension is the same check the guard makes at startup, so a mismatched artifact fails here
    too rather than producing quietly meaningless scores.
    """
    settings = _luotsi_settings(ctx)
    model_path = _embedding_model(settings)
    injection = _injection_config(settings)

    artifact_path = vectors or _vectors_path(settings)
    if not artifact_path.exists():
        raise click.ClickException(
            f"No artifact at {artifact_path}. A run trains one at start; to get one now use `meri feedback "
            f"train-guard`, or pass --vectors to read a different file."
        )

    embedder = _embedder(model_path)
    return _Guard(
        embed=embedder,
        artifact=GuardVectors.load(artifact_path, dim=len(embedder("dimension probe"))),
        floor=injection.floor if floor is None else floor,
        margin=injection.margin if margin is None else margin,
        path=artifact_path,
    )


@click.group("feedback")
def cli() -> None:
    """Reader feedback tooling: provision the embedding model, train the injection guard and probe it."""


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
        # Overwriting in place changes the weights but not the identifier or the dimension, so the injection
        # guard's artifact still reads as current and would keep centroids built from the old model.
        click.echo("If this replaced an existing model, run `meri feedback train-guard` to rebuild the guard.")
    else:
        click.echo(
            f"Saved to {destination}. Add to your configuration:\n\n  luotsi:\n    embedding_model: "
            f"{hub_id_of(destination) or destination.resolve()}\n"
        )


@cli.command("train-guard")
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the artifact. Defaults to the path the guard itself reads.",
)
@click.option(
    "--data",
    "data_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Labeled exemplar file. Repeat to add languages. Defaults to the configured `data`, which is the "
    "catalog shipped with Luotsi unless a deployment names its own.",
)
@click.option(
    "--cluster-threshold",
    type=float,
    help="Similarity at which two exemplars of one label share a centroid. Defaults to the configured "
    "`cluster_threshold`.",
)
@click.pass_context
def train_guard(
    ctx: click.Context, output: Path | None, data_files: tuple[Path, ...], cluster_threshold: float | None
) -> None:
    """
    Train the injection guard's centroid artifact, and report what it can and cannot do.

    A run trains this artifact for itself at start, whenever it is missing or its inputs have moved, so this
    command is not how you GET one — it is how you REVIEW one. It prints the training report, which is the
    expensive half and the half a person reads: the clusters, the benign centroids close enough to veto an
    attack centroid, and the self-check.

    Deterministic. Writing where the guard reads means a run will then find this artifact current and leave
    it alone.
    """
    settings = _luotsi_settings(ctx)
    model_path = _embedding_model(settings)

    destination = output or _vectors_path(settings)

    # Overrides apply by building the configuration this command was asked for, so training goes through the
    # one function that writes an artifact. Doing it by hand here is what let this command skip the
    # cannot-classify check and write non-atomically over a file a running guard was reading.
    injection = _injection_config(settings).model_copy(
        update={
            "data": list(data_files) or _injection_config(settings).data,
            **({} if cluster_threshold is None else {"cluster_threshold": cluster_threshold}),
        }
    )
    exemplars = parse_files(injection.data)
    # The same identity the run records, not the directory name: writing a different one here would have this
    # command and every run permanently disagree about whose artifact this is.
    identity = model_identity(settings)
    click.echo(f"Training on {len(exemplars)} exemplar(s) with {identity} ...")

    embed = _embedder(model_path)
    artifact, _ = ensure_guard_vectors(injection, embed, identity, destination, force=True)

    # The report is the reason to run this by hand rather than let a run provision the same artifact: it is the
    # expensive half, and it is the half a person reads.
    report = training_report(artifact, exemplars, embed, floor=injection.floor, margin=injection.margin)

    click.echo(f"\nWrote {len(artifact.centroids)} centroid(s) to {destination}\n")
    click.echo(report.render())

    if report.misclassifications:
        click.echo("\nThe self-check found misclassifications. Read them before deploying this artifact.")


@cli.command("probe")
@click.argument("data_files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--vectors",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Artifact to classify against. Defaults to the configured injection guard's `vectors` path.",
)
@click.option("--floor", type=float, help="Override the configured decision floor.")
@click.option("--margin", type=float, help="Override the configured decision margin.")
@click.option("--sweep", "with_sweep", is_flag=True, help="Also print error rates over a grid of thresholds.")
@click.option("--quiet", is_flag=True, help="Print the rates only, not the lines behind them.")
@click.pass_context
def probe(
    ctx: click.Context,
    data_files: tuple[Path, ...],
    vectors: Path | None,
    floor: float | None,
    margin: float | None,
    with_sweep: bool,
    quiet: bool,
) -> None:
    """
    Classify labeled lines with a trained artifact and report what it gets wrong.

    The training report only describes the data the artifact was built from, where every exemplar is its own
    centroid. This measures lines it was NOT trained on: an attack line that survives is an evasion, a benign
    line that drops is a reader being silenced. Both are what the next round of exemplars should be written
    from.

    DATA_FILES are labeled exemplar files, in the same format `train-guard` reads.
    """
    guard = _load_guard(ctx, vectors, floor, margin)

    lines = parse_files(list(data_files))
    click.echo(f"Probing {len(lines)} line(s) against {guard.path} ...\n")

    embedded = embed_lines(lines, guard.embed)
    click.echo(evaluate(embedded, guard.artifact, guard.floor, guard.margin).render(show_lines=not quiet))

    if with_sweep:
        click.echo()
        click.echo(sweep(embedded, guard.artifact, guard.floor, guard.margin).render())


@cli.command("check")
@click.argument("text")
@click.option(
    "--vectors",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Artifact to classify against. Defaults to the configured injection guard's `vectors` path.",
)
@click.option("--floor", type=float, help="Override the configured decision floor.")
@click.option("--margin", type=float, help="Override the configured decision margin.")
@click.pass_context
def check(ctx: click.Context, text: str, vectors: Path | None, floor: float | None, margin: float | None) -> None:
    """
    Say whether one message would be dropped, and why.

    TEXT is classified exactly as a reader's comment would be — sanitized, embedded, and put through the same
    decision — so this answers what the deployment would really do with it. The two centroids the decision
    turned on are printed with it, because "dropped" on its own does not say whether the message was near an
    attack or merely far from everything benign.
    """
    guard = _load_guard(ctx, vectors, floor, margin)
    click.echo(explain(text, guard.embed, guard.artifact, guard.floor, guard.margin).render())


@cli.command("show")
@click.argument("url")
@click.option("--limit", type=int, help="Most message groups to render. Defaults to `max_messages_per_article`.")
@click.pass_context
def show(ctx: click.Context, url: str, limit: int | None) -> None:
    """
    Render the reader feedback for one article URL as the model receives it.

    Pulls from the configured sources, runs the guardrail chain over this article's feedback, consolidates
    repeated messages and renders the `feedback.md.j2` block — so what is printed is the untrusted-data section
    of the prompt itself, after everything that filters it. Use it to see what a reader's comment actually
    became, or why an article's feedback is not reaching the model.

    Diagnostics go to stderr, so stdout is the prompt block alone and can be piped.
    """
    # Imported here: `meri.feedback` pulls in the article model and the clusterer, and none of the other
    # commands in this group need either.
    from jinja2 import Template
    from pydantic import ValidationError

    from ..article import Article
    from ..feedback import build_matcher, feedback_for_article
    from ..prompts import PROMPT_TEMPLATE_FEEDBACK, get_prompt_template

    settings = _luotsi_settings(ctx)

    # Validated rather than constructed, so a malformed URL is a command error and not a traceback. Only the
    # URL is needed: feedback is matched by signature, so nothing has to be fetched or extracted.
    try:
        article = Article.model_validate({"urls": [{"href": url}]})
    except ValidationError as e:
        raise click.ClickException(f"Not a usable article URL: {url}") from e

    signature = article.urls[0].signature

    # This command reads; it does not provision. A guard whose vectors no longer match its exemplars refuses
    # to build, and that refusal is the answer to "why is feedback not reaching the model" — so it is worth a
    # sentence rather than a traceback.
    try:
        matcher = build_matcher(settings)
    except FileNotFoundError as e:
        # The chain builds the model before the vectors, so an unprovisioned deployment fails here first.
        raise click.ClickException(
            f"{e}\n\n`meri feedback download-model` fetches it, and `meri run` does the same at start."
        ) from e
    except ValueError as e:
        raise click.ClickException(f"{e}\n\nA run provisions them; `meri feedback train-guard` does it now.") from e

    # Read before the aggregate is built: the matcher guards this signature's bucket on first match and
    # replaces it, so afterwards only the survivors are left to count.
    matched = len(matcher.map.get(signature, []))
    aggregate = feedback_for_article(
        matcher,
        article,
        MessageClusterer.from_settings(settings),
        settings.max_messages_per_article if limit is None else limit,
    )

    click.echo(f"url signature : {signature}", err=True)
    click.echo(f"feedback kept : {matched} item(s) matched, {len(matcher.map.get(signature, []))} survived the guardrails", err=True)

    if aggregate is None:
        # The signature above is the thing to check first: feedback is matched by it, not by the URL text.
        raise click.ClickException(f"No feedback matches {url}.")

    click.echo(f"this article  : {len(aggregate.titles)} title(s) rated, {len(aggregate.items)} message group(s)\n", err=True)
    click.echo(Template(get_prompt_template(PROMPT_TEMPLATE_FEEDBACK)).render(feedback=aggregate).strip())


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
