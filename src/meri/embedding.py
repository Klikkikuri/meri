"""
The process-wide embedding model: resolving, provisioning and loading it.

Everything that embeds text — Luotsi's consolidation and injection guard, the title pipeline's drift guard —
gets its callable from here, so the model is named once in `embedding:`, fetched once at the start of a run
and loaded once per process. Nothing else fetches or loads a model.

A configured model is a hard requirement. Import and load errors propagate, so a misconfigured deployment fails
loudly at start instead of quietly running in a weaker mode.
"""

import os
import shutil
import tempfile
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from niitti import get_logger

from .settings.embedding import hub_id_of

if TYPE_CHECKING:
    from .settings.settings import Settings

Vector = np.ndarray
Embedder = Callable[[str], Vector]

logger = get_logger(__name__)

INSTALL_HINT = "Install model2vec to use an embedding model: pip install 'model2vec'"


def download_model(target_dir: Path, model: str) -> None:
    """
    Fetch a Model2Vec model from the hub and save it locally.

    Fetching is a write and a network call, so it stays out of the loader: a host provisions deliberately — at
    start-up or by hand — and everything else only reads what it finds.

    :param target_dir: Where to save the model. Created when it does not exist.
    :param model: Hub identifier of the model to fetch.
    :raises ImportError: When model2vec is not installed.
    """
    try:
        from model2vec import StaticModel
    except ImportError as e:
        raise ImportError(INSTALL_HINT) from e

    logger.info("Downloading embedding model", model=model, target=str(target_dir))
    target_dir.mkdir(parents=True, exist_ok=True)
    StaticModel.from_pretrained(model).save_pretrained(str(target_dir))


def model_exists(path: Path) -> bool:
    """
    Whether a directory already holds a loadable model.

    Asks what the loader asks, through model2vec's own folder layouts, rather than looking for a file this
    package picks: `save_pretrained` writes the embeddings BEFORE the tokenizer and the config, so a check for
    the embeddings alone would read an interrupted download as a finished model for good.

    :param path: Directory that should hold a saved model. It can be absent.
    :raises ImportError: When model2vec is not installed.
    :return: True when the directory holds every file one of the layouts needs.
    """
    try:
        from model2vec.persistence.datamodels import FOLDER_LAYOUTS
    except ImportError as e:
        raise ImportError(INSTALL_HINT) from e

    return any(layout.with_parent(path).is_valid() for layout in FOLDER_LAYOUTS)


@cache
def load_embedder(path: Path) -> Embedder:
    """
    Load a local Model2Vec static embedding model.

    Cached on the resolved path, so every caller in the process shares one loaded model.

    :param path: Directory holding the saved Model2Vec model.
    :raises ImportError: When model2vec is not installed.
    :raises FileNotFoundError: When the directory holds no model.
    :return: A callable that turns one string into its embedding vector.
    """
    try:
        from model2vec import StaticModel
    except ImportError as e:
        raise ImportError(INSTALL_HINT) from e

    # model2vec reads a path it cannot find as a hub identifier, and an absolute path fails hub validation
    # rather than the directory check the caller actually made. Say what is really wrong.
    if not model_exists(path):
        raise FileNotFoundError(f"No Model2Vec model in {path}. Provision one there before loading it.")

    logger.info("Loading embedding model", path=str(path))
    model = StaticModel.from_pretrained(str(path))

    def embed(text: str) -> Vector:
        return model.encode([text])[0]

    return embed


def ensure_model(path: Path) -> bool:
    """
    Fetch the configured model when its directory holds none. WRITES, and reaches the hub.

    What `meri run` calls so that a cold deployment provisions itself, the way it already trains its own guard
    vectors. The hub identifier is the one the directory resolves from, so an operator names a model once and a
    run needs nothing else.

    Publishes by rename, for the same reason :func:`~meri.luotsi.guards.provision.write_artifact` does: runs
    overlap, and a second process must never read a half-written model.

    :param path: The resolved model directory, from `embedding.model`.
    :raises FileNotFoundError: When the directory names no hub model, or holds a partial one.
    :return: True when it fetched a model, False when one was already there.
    """
    if model_exists(path):
        return False

    hub_id = hub_id_of(path)
    if not hub_id or hub_id.count("/") != 1:
        raise FileNotFoundError(
            f"No model in {path}, and nothing says where to fetch one: it is not a directory a hub identifier "
            f"resolves to. Provision it with `meri feedback download-model {path}`, or name a hub model in "
            f"`embedding.model`."
        )

    if path.exists() and any(path.iterdir()):
        # An interrupted fetch, so this is not a directory to replace unasked; `download-model` writes in place
        # and is the deliberate way to say "overwrite whatever is there".
        raise FileNotFoundError(
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


def embedding_model(settings: "Settings") -> Path | None:
    """The directory the configured model loads from, or None when `embedding:` is null."""
    return settings.embedding.path if settings.embedding else None


def model_identity(settings: "Settings") -> str | None:
    """
    What the injection guard's artifact records as the model that produced it.

    The hub identifier when the model came from the hub, so `minishlab/potion-multilingual-128M` and
    `otherorg/potion-multilingual-128M` are distinguishable — the bare directory name is not, and neither is the
    dimension, so the guard would score one model's centroids against the other's embeddings. A model provisioned
    by other means keeps its directory name rather than its absolute path, which would be machine-specific and
    would make merely moving an identical model read as a different one.
    """
    path = embedding_model(settings)
    return None if path is None else (hub_id_of(path) or path.name)


def provision_model(settings: "Settings", download: bool) -> bool:
    """
    Make the configured model loadable, then load it. The one place a run fetches or loads a model.

    :param settings: Meri's settings; `embedding:` names the model.
    :param download: Fetch from the hub when the directory holds no model. Off, an unprovisioned model is an
        error rather than a network call.
    :raises FileNotFoundError: When no model can be loaded from the configured directory.
    :return: True when a model was fetched, so the caller can rebuild what was bound to the old weights.
    """
    path = embedding_model(settings)
    if path is None:
        return False

    with logger.span("provision_embedding_model", model=model_identity(settings)) as span:
        fetched = ensure_model(path) if download else False
        span.set_attribute("fetched", fetched)
        load_embedder(path)
        if fetched:
            logger.info("Fetched embedding model", model=model_identity(settings), path=str(path))
    return fetched


def get_embedder(settings: "Settings") -> Embedder | None:
    """
    The process-wide embedder, or None when no model is configured.

    :raises FileNotFoundError: When the configured directory holds no model.
    """
    path = embedding_model(settings)
    return load_embedder(path) if path else None
