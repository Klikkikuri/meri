"""
Provisioning for the injection guard's trained artifact.

The guard classifies against a centroid artifact that no deployment can inherit — it is bound to the embedding
model that produced it — so every deployment builds its own. This module is the ONE place that builds and
publishes it. The guard itself only reads.

That split is deliberate, and it follows the one this package already makes for the embedding model:
:func:`~meri.luotsi.embeddings.download_model` fetches, :func:`~meri.luotsi.embeddings.load_embedder` loads and fails if
nothing is there. A loader that quietly provisions is a loader that writes to disk when you thought you were
reading, and it gives a host no way to say "check, but do not change anything".

The host decides WHEN. This module decides WHAT and refuses to publish something that cannot classify.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ..settings.guardrails import InjectionConfig
from .labeled import BENIGN, LabeledLine, parse_files
from .trainer import source_digest, train_centroids
from .vectors import GuardVectors

if TYPE_CHECKING:
    from ..embeddings import Embedder

logger = logging.getLogger(__name__)


class Inspection(NamedTuple):
    """What is on disk for one injection configuration, and whether it can be used."""

    artifact: GuardVectors | None
    """The artifact found, or None when there is nothing usable at the path."""

    reason: str | None
    """Why it cannot be used as it stands, or None when it is current."""

    lines: list[LabeledLine]
    """The exemplars the configuration names, already parsed."""

    digest: str
    """The fingerprint those exemplars and the threshold produce."""


def artifact_path(config: InjectionConfig, fallback: Path | None) -> Path:
    """
    Where this guard's artifact lives.

    :param config: The guard's own configuration; its `vectors` wins when set.
    :param fallback: The destination the host application resolved, for a guard that names none. The default
        chain holds a bare `InjectionConfig` that no configuration file can reach, so this is its only route.
    :raises ValueError: When neither names a destination.
    """
    path = config.vectors or fallback
    if path is None:
        raise ValueError(
            "The injection guard has nowhere to keep its trained artifact. Point `vectors` at a file, or "
            "leave the guard out of `guardrails`."
        )
    return path


def read_artifact(path: Path) -> GuardVectors | None:
    """
    The artifact on disk, or None when there is nothing usable there.

    Unreadable counts the same as absent, so a file left torn by an interrupted write is rebuilt rather than
    failing the run. Loaded without the dimension check: a mismatch is a reason to rebuild, not a hard error.
    """
    try:
        return GuardVectors.load(path)
    except (OSError, ValueError) as e:
        logger.debug("No usable guard vectors at %s: %s", path, e)
        return None


def inspect(config: InjectionConfig, embed: "Embedder", model_name: str | None, path: Path) -> Inspection:
    """
    Read the artifact at `path` and say whether it serves this configuration.

    Pure: it reads and compares, and never writes. This is what lets a host check without changing anything.

    :raises OSError: When a configured exemplar file cannot be read.
    :raises ValueError: When an exemplar file is malformed.
    """
    lines = parse_files(config.data)
    digest = source_digest(lines, config.cluster_threshold)
    artifact = read_artifact(path)

    return Inspection(artifact, _stale_reason(artifact, digest, len(embed("dimension probe")), model_name), lines, digest)


def _stale_reason(
    artifact: GuardVectors | None, digest: str, live_dim: int, model_name: str | None
) -> str | None:
    """Why the artifact needs rebuilding, or None when it is current."""
    if artifact is None:
        return "no usable artifact yet"
    if artifact.dim != live_dim:
        return f"trained at dimension {artifact.dim}, the model produces {live_dim}"
    if model_name and artifact.model_name != model_name:
        return f"trained on {artifact.model_name!r}, the configured model is {model_name!r}"
    if artifact.source_digest != digest:
        return "the exemplars or the clustering threshold have changed"
    return None


def check_classifies(artifact: GuardVectors, path: Path) -> None:
    """
    Refuse an artifact that cannot make the decision the guard exists to make.

    A catalog of only benign lines trains without complaint and reports a clean self-check — no
    misclassifications, no shadows — and then passes every attack, because `classify` compares against a
    nearest drop centroid that does not exist. `data` REPLACES the shipped catalog, so one misconfigured file
    would silently switch a security control off. The mirror case matters too: with no benign class nothing can
    ever veto a drop, and ordinary readers are silenced wholesale.

    Called on reading as well as on training, because both routes end with a guard using the result.
    """
    drops = [centroid for centroid in artifact.centroids if centroid.label != BENIGN]
    if not drops or not artifact.by_label(BENIGN):
        raise ValueError(
            f"The injection guard's vectors hold {len(drops)} drop centroid(s) and "
            f"{len(artifact.by_label(BENIGN))} benign one(s), which cannot classify anything. Check the "
            f"`data` files, and the artifact at {path}."
        )


def write_artifact(artifact: GuardVectors, path: Path) -> None:
    """
    Publish the artifact, atomically.

    A unique temporary name, not one derived from the destination: two processes starting together train the
    same bytes, but sharing one temporary path lets the second truncate what the first is about to rename into
    place, publishing a torn artifact to everyone else. Determinism makes the content safe, not the
    interleaving.

    :raises OSError: When the destination cannot be written. A destination that is set but unusable is a broken
        deployment, so this propagates rather than leaving the caller to carry on in memory.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(artifact.dump())
        # `mkstemp` opens 0600 and `os.replace` carries that onto the destination, so without this the artifact
        # would silently narrow to one user — including the same deployment after a `PUID` change, on a volume
        # this process may then be unable to rewrite.
        os.chmod(temporary, 0o644 & ~_umask())
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise

    logger.info("Wrote %d guard centroid(s) to %s", len(artifact.centroids), path)


def ensure_guard_vectors(
    config: InjectionConfig,
    embed: "Embedder",
    model_name: str | None,
    destination: Path,
    *,
    force: bool = False,
) -> tuple[GuardVectors, str | None]:
    """
    Bring the artifact at `destination` up to date, training and publishing it when it is not.

    The one operation that writes one. `meri feedback train-guard` and a host's start-up provisioning both come
    through here, so the validity check and the atomic write cannot apply to one route and not the other.

    Building the centroids is the cheap half of training — about 0.2s on the shipped catalog — because the
    expensive self-check belongs to :func:`~meri.luotsi.guards.trainer.training_report`, which is for a person.

    :param config: The injection guard's configuration.
    :param embed: Shared embedding callable.
    :param model_name: Name of the embedding model, recorded in the artifact.
    :param destination: Where to publish it, from :func:`artifact_path`.
    :param force: Retrain even when the artifact on disk is current.
    :raises ValueError: When the catalog cannot classify, or training is needed with no `model_name` to record.
        Nothing is written in either case.
    :raises OSError: When the artifact cannot be written.
    :return: The current artifact, and why it was retrained — None when the file was already current.
    """
    found = inspect(config, embed, model_name, destination)
    reason = found.reason or ("forced" if force else None)

    # `reason is None` already implies an artifact was found; saying so explicitly narrows the type honestly
    # and leaves training as the fallback if that ever stops holding.
    if reason is None and found.artifact is not None:
        check_classifies(found.artifact, destination)
        return found.artifact, None

    logger.info("Training guard vectors from %d exemplar(s): %s", len(found.lines), reason)
    if not model_name:
        raise ValueError(
            "The injection guard cannot record which model it trained with. Set `embedding_model`, or point "
            "`vectors` at an artifact someone else trained."
        )

    artifact = train_centroids(found.lines, embed, model_name, config.cluster_threshold)
    check_classifies(artifact, destination)
    write_artifact(artifact, destination)
    return artifact, reason


def _umask() -> int:
    """The process umask. Only readable by setting it, so it is put straight back."""
    current = os.umask(0)
    os.umask(current)
    return current
