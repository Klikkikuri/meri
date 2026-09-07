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

logger = logging.getLogger(__name__)

INSTALL_HINT = "Install the semantic extra to use an embedding model: pip install 'luotsi[semantic]'"


@cache
def load_embedder(path: Path) -> "Callable[[str], np.ndarray]":
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

    def embed(text: str) -> "np.ndarray":
        return model.encode([text])[0]

    return embed
