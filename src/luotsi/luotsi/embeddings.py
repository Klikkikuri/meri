"""
Shared embedding model loader.

Both the injection guard and the message clusterer embed text. They must share one loaded model per process: the
model is a few hundred megabytes and loading it twice buys nothing.

A configured model is a hard requirement. Import and load errors propagate, so a misconfigured deployment fails
loudly at start instead of quietly running in a weaker mode.
"""

import logging
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    Vector = np.ndarray
    Embedder = Callable[[str], Vector]

logger = logging.getLogger(__name__)

INSTALL_HINT = "Install the semantic extra to use an embedding model: pip install 'luotsi[semantic]'"


def download_model(target_dir: Path, model: str) -> None:
    """
    Fetch a Model2Vec model from the hub and save it locally.

    Provisioning the model this way keeps hub access out of the runtime container.

    :param target_dir: Where to save the model. Created when it does not exist.
    :param model: Hub identifier of the model to fetch.
    :raises ImportError: When the `semantic` extra is not installed.
    """
    try:
        from model2vec import StaticModel
    except ImportError as e:
        raise ImportError(INSTALL_HINT) from e

    logger.info("Downloading %s", model)
    target_dir.mkdir(parents=True, exist_ok=True)
    StaticModel.from_pretrained(model).save_pretrained(str(target_dir))


@cache
def load_embedder(path: Path) -> "Embedder":
    """
    Load a local Model2Vec static embedding model.

    Cached on the resolved path, so every caller in the process shares one loaded model.

    :param path: Directory holding the saved Model2Vec model.
    :raises ImportError: When the `semantic` extra is not installed.
    :return: A callable that turns one string into its embedding vector.
    """
    try:
        from model2vec import StaticModel
    except ImportError as e:
        raise ImportError(INSTALL_HINT) from e

    logger.info("Loading embedding model from %s", path)
    model = StaticModel.from_pretrained(str(path))

    def embed(text: str) -> "Vector":
        return model.encode([text])[0]

    return embed
