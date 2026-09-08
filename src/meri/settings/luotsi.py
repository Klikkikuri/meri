"""
Where Luotsi's embedding model comes from.

Luotsi takes a directory and nothing else — it carries no default model and knows no XDG layout. Naming the
model, and turning that name into a directory under Meri's data directory, is this application's job, so the
whole decision lives here: the default identifier, the layout, and the resolution the configuration goes
through before Luotsi ever sees it.
"""

from pathlib import Path

from niitti.paths import data_dir

from .const import APP_NAME

DEFAULT_EMBEDDING_MODEL = "minishlab/potion-multilingual-128M"
"""101 languages in one 256-dimension vector space, so Finnish and English feedback share a metric."""

EMBEDDING_SUBDIR = "embedding"
"""Where under the data directory provisioned models live, one directory per hub identifier."""


def embedding_dir() -> Path:
    """
    The directory that holds every model provisioned from the hub.

    :return: `<data dir>/embedding`. It is not created and can be absent.
    """
    return data_dir(APP_NAME) / EMBEDDING_SUBDIR


def default_vectors() -> Path:
    """
    Where the injection guard keeps its trained artifact when no guard names a path of its own.

    Luotsi trains the artifact itself but knows no directory layout, so the location is this application's
    decision, like the model directory above. It lands in the data directory, which a container mounts as a
    persistent volume, so a rebuilt image with new exemplars retrains once and keeps the result.

    :return: `<data dir>/guard-vectors.json`. Neither it nor its parent need exist yet.
    """
    return data_dir(APP_NAME) / "guard-vectors.json"


def is_hub_id(model: str) -> bool:
    """
    Whether a configured model names a hub model rather than a directory on this machine.

    Hub identifiers are `org/name`, which looks like a relative path, so the two are told apart by their start:
    a path is absolute or explicitly relative (`.`, `..`, `~`), anything else is a hub identifier.

    :param model: The configured `luotsi.embedding_model` value.
    :return: True for a hub identifier.
    """
    return not (model.startswith(("~", ".")) or Path(model).is_absolute())


def model_dir(model: str) -> Path:
    """
    The local directory a configured model loads from.

    A hub identifier resolves under the data directory, so an operator names a model and never a path;
    `meri feedback download-model` saves into exactly this directory and every run loads from it. A path is
    used as given, for a model provisioned by other means.

    :param model: Hub identifier, or the path of a directory holding a saved model.
    :return: Directory of the model. It is not created and can be absent.
    """
    return embedding_dir() / model if is_hub_id(model) else Path(model).expanduser()


def hub_id_of(path: Path) -> str | None:
    """
    The hub identifier a resolved directory came from, when it came from one.

    The inverse of :func:`model_dir`, for the provisioning command: settings hold the resolved directory, and
    `download-model` needs the identifier to fetch.

    :param path: A resolved model directory.
    :return: The hub identifier, or None for a directory outside the data directory.
    """
    try:
        return str(path.relative_to(embedding_dir()))
    except ValueError:
        return None
