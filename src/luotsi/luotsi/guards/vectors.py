"""
The trained artifact the injection guard classifies against.

A small JSON file: one centroid per cluster of labeled exemplars. It is diffable and readable, so a reviewer can
see what the guard was taught before it ships.
"""

import logging
from pathlib import Path

from pydantic import BaseModel, Field

PRECISION = 6
"""Decimals kept per vector component. Far finer than the decision thresholds, and a third of the file size."""

logger = logging.getLogger(__name__)


class Centroid(BaseModel):
    """The center of one cluster of same-label exemplars."""

    label: str = Field(description="Label of the exemplars in the cluster.")
    vector: list[float] = Field(description="L2-normalized mean of the members' embeddings.")
    size: int = Field(description="How many exemplars the cluster holds.")
    representative: str = Field(description="One member's text, to name the cluster in reports and logs.")


class GuardVectors(BaseModel):
    """A trained injection classifier."""

    model_name: str = Field(description="Embedding model the vectors were trained with.")
    dim: int = Field(description="Embedding dimension. Checked against the live model at load.")
    cluster_threshold: float = Field(description="Similarity at which exemplars joined one cluster.")
    centroids: list[Centroid] = Field(description="The trained centroids, in training order.")

    def dump(self) -> str:
        """
        Render the artifact with one centroid per line.

        Compact enough to commit, and a diff still names the centroids that changed rather than the thousands of
        individual numbers inside them.
        """
        header = {name: getattr(self, name) for name in ("model_name", "dim", "cluster_threshold")}
        head = ",".join(f"{key!r}: {value!r}".replace("'", '"') for key, value in header.items())
        lines = ",\n  ".join(
            centroid.model_copy(update={"vector": [round(v, PRECISION) for v in centroid.vector]}).model_dump_json()
            for centroid in self.centroids
        )
        return "{" + head + ', "centroids": [\n  ' + lines + "\n]}\n"

    def by_label(self, label: str) -> list[Centroid]:
        """Centroids carrying one label."""
        return [centroid for centroid in self.centroids if centroid.label == label]

    @classmethod
    def load(cls, path: Path, dim: int | None = None) -> "GuardVectors":
        """
        Read a trained artifact and check it against the live embedding model.

        :param path: The artifact file.
        :param dim: Dimension of the live model's output, when known.
        :raises ValueError: When the artifact was trained at a different dimension.
        :return: The loaded artifact.
        """
        vectors = cls.model_validate_json(path.read_text(encoding="utf-8"))

        if dim is not None and dim != vectors.dim:
            raise ValueError(
                f"Guard vectors in {path} have dimension {vectors.dim}, but the embedding model produces {dim}. "
                f"Retrain them with `luotsi train-guard`."
            )

        logger.info("Loaded %d guard centroid(s) trained on %s", len(vectors.centroids), vectors.model_name)
        return vectors
