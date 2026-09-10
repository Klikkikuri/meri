"""Configuration schema for message consolidation."""

from pydantic import BaseModel, Field


class ClusteringSettings(BaseModel):
    """
    Tuning for :class:`meri.luotsi.cluster.MessageClusterer`.

    The threshold measures different things per mode — character-3-gram cosine without an embedding model,
    embedding cosine with one — so it needs tuning per mode against real feedback.
    """

    similarity_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Cosine similarity at which two messages join the same group."
    )
