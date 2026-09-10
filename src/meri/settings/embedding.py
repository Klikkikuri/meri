"""
Where the embedding model comes from.

One Model2Vec model serves everything in Meri that embeds text: Luotsi's message consolidation and injection
guard, and the title pipeline's drift guard. It is named here, once, and resolved to a directory under Meri's
data directory. The loading and provisioning that follow live in :mod:`meri.embedding`.
"""

from pathlib import Path
from typing import Literal

from niitti.paths import data_dir
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .const import APP_NAME

DEFAULT_EMBEDDING_MODEL = "minishlab/potion-multilingual-128M"
"""101 languages in one 256-dimension vector space, so Finnish and English text share a metric."""

EMBEDDING_SUBDIR = "embedding"
"""Where under the data directory provisioned models live, one directory per hub identifier."""


def embedding_dir() -> Path:
    """
    The directory that holds every model provisioned from the hub.

    :return: `<data dir>/embedding`. It is not created and can be absent.
    """
    return data_dir(APP_NAME) / EMBEDDING_SUBDIR


def is_hub_id(model: str) -> bool:
    """
    Whether a configured model names a hub model rather than a directory on this machine.

    Hub identifiers are `org/name`, which looks like a relative path, so the two are told apart by their start:
    a path is absolute or explicitly relative (`.`, `..`, `~`), anything else is a hub identifier.

    :param model: The configured `embedding.model` value.
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


class LocalEmbeddingSettings(BaseModel):
    """
    A Model2Vec model on this machine.

    `provider` is a literal so that a second provider can join a discriminated union without changing what a
    configuration written today means.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["local"] = "local"

    model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        description="Hub identifier, resolved under the data directory, or the path of a directory holding a "
        "saved model (absolute, or starting with `.` or `~`).",
    )

    @property
    def path(self) -> Path:
        """The directory the model loads from. It need not exist yet: `meri run` fills it."""
        return model_dir(self.model)

    @field_validator("model")
    @classmethod
    def _path_must_be_a_directory(cls, value: str) -> str:
        """
        A configured directory need not hold a model yet — it need only be able to.

        A path that exists as something other than a directory can never work, and this is the earliest place
        to say so. Whether the directory really holds a loadable model is settled when it is loaded, at the
        start of a run, so a configured model never degrades silently into the built-in mode.
        """
        resolved = model_dir(value)
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(f"embedding model is not a directory: {resolved}")
        return value


EmbeddingSettings = LocalEmbeddingSettings
"""The configured provider. Becomes an `Annotated` union discriminated on `provider` with a second provider."""
