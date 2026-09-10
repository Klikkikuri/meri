"""
Prompt injection guard.

Drops feedback that tries to instruct the language model instead of commenting on a title. Detection keys off
:attr:`FeedbackItem.original` so that the sanitizer and the redactor cannot remove the evidence it looks for.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ..abc import FeedbackItem, Guardrail
from ..settings.guardrails import InjectionConfig
from .labeled import BENIGN
from .provision import artifact_path, check_classifies, inspect
from .sanitize import SanitizeGuard
from .vectors import Centroid, GuardVectors

if TYPE_CHECKING:
    from ..embeddings import Embedder, Vector

logger = logging.getLogger(__name__)


class Match(NamedTuple):
    """The closest centroid of one side of the decision, and how close it is."""

    similarity: float
    centroid: Centroid | None

    @property
    def representative(self) -> str | None:
        """The exemplar text naming the centroid, for reports and logs."""
        return self.centroid.representative if self.centroid else None


def nearest(vector: "Vector", artifact: GuardVectors) -> tuple[Match, Match]:
    """
    The closest drop centroid and the closest benign centroid to one embedded message.

    Split out of :func:`classify` so that a caller explaining a decision reads the same numbers the decision was
    made from, rather than recomputing them and risking a second, divergent implementation of the rule.

    :return: The nearest non-benign centroid, then the nearest benign one.
    """
    import numpy as np

    # Centroids are stored L2-normalized, so the dot product is the cosine only once the message vector is too.
    # A model whose config omits `normalize` returns raw vectors, and an unscaled similarity makes the floor and
    # the margin mean nothing — the guard would pass attacks through without a word.
    message = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(message))
    if norm:
        message = message / norm

    def closest(wanted_benign: bool) -> Match:
        scored = [
            Match(float(np.dot(message, centroid.vector)), centroid)
            for centroid in artifact.centroids
            if (centroid.label == BENIGN) is wanted_benign
        ]
        # Keyed on the similarity alone: tuple ordering would fall through to comparing centroids on a tie.
        return max(scored, key=lambda match: match.similarity, default=Match(0.0, None))

    return closest(wanted_benign=False), closest(wanted_benign=True)


def classify(vector: "Vector", artifact: GuardVectors, floor: float, margin: float) -> tuple[bool, str | None]:
    """
    Decide whether one embedded message is an attack.

    Every drop class is a curated, closed catalog, so nearness to one is evidence. The benign class is
    open-world — no data set can enumerate what readers legitimately say — so distance from it is never evidence
    of an attack. The rule is therefore one-sided: drop only when the message is near-certainly an attack (the
    floor) AND no benign exemplar sits nearly as close (the margin). Doubt goes to the reader.

    :return: Whether to drop, and the benign representative that vetoed a drop, if one did.
    """
    attack, veto = nearest(vector, artifact)

    if attack.similarity < floor:
        return False, None
    if attack.similarity - veto.similarity < margin:
        return False, veto.representative
    return True, None


class InjectionGuard(Guardrail):
    """
    Drops messages that try to instruct the model.

    One tier: nearest-centroid comparison against a trained attack catalog, so rewordings are caught as well as
    the phrasings themselves. It needs an embedding model and a usable artifact, and refuses to be built
    without them — a guard that is configured but cannot classify is a broken deployment, not a degraded one. A
    deployment that runs without an embedding model leaves `injection` out of its `guardrails` list instead.

    It READS its artifact and never writes one. Building it belongs to
    :func:`~meri.luotsi.guards.provision.ensure_guard_vectors`, which a host calls deliberately — the same division
    :func:`~meri.luotsi.embeddings.download_model` and :func:`~meri.luotsi.embeddings.load_embedder` already make for the
    embedding model. What the guard keeps is the refusal: an artifact whose exemplars, threshold, dimension or
    model no longer match is rejected by name rather than used, so nothing can classify against stale vectors.
    """

    name = "injection"

    def __init__(
        self,
        config: InjectionConfig,
        embed: "Embedder | None" = None,
        model_name: str | None = None,
        vectors: Path | None = None,
    ) -> None:
        """
        :param config: Guard configuration.
        :param embed: Shared embedding callable.
        :param model_name: Name of the configured embedding model, compared against the artifact's.
        :param vectors: Destination the host application resolved, for a guard that names none of its own.
        :raises ValueError: When there is no embedding model, nowhere to read the artifact from, or the
            artifact is stale or cannot classify.
        """
        if embed is None:
            raise ValueError(
                "The injection guard needs an embedding model. Set `embedding_model`, or leave the guard out of "
                "`guardrails`."
            )

        self.config = config
        self.embed = embed
        self.model_name = model_name

        path = artifact_path(config, vectors)
        found = inspect(config, embed, model_name, path)
        if found.reason is not None or found.artifact is None:
            raise ValueError(
                f"The injection guard cannot use the vectors at {path}: {found.reason}. Provision them with "
                f"`meri feedback train-guard`."
            )

        # Checked on the way in, not only on the way out of training: an artifact written by `train-guard`
        # arrives with a digest that matches its exemplars, so it looks perfectly current. Validating only what
        # this process trained would wave a non-classifying catalog straight through.
        check_classifies(found.artifact, path)
        self.vectors = found.artifact

    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """
        Keep only the items the centroids do not flag.

        A flagged item is removed whole, so it contributes neither a message nor a vote nor a regeneration trigger.
        """
        kept = [item for item in items if not self._is_injection(item)]

        dropped = len(items) - len(kept)
        if dropped:
            logger.warning("%s: dropped %d message(s) as injection attempts", self.name, dropped)
        return kept

    def _is_injection(self, item: FeedbackItem) -> bool:
        """
        Test one item against the trained centroids.

        Keys off the ORIGINAL message, so it is immune to whatever an earlier guard rewrote. Only the sanitizer's
        cleaning is applied, on a copy, because invisible padding perturbs the embedding without being anything a
        reader typed. Message bodies never reach the log — only counts, and the curated exemplar text of a vetoing
        centroid.
        """
        if not item.original.message.strip():
            return False

        dropped, vetoed_by = classify(
            self.embed(SanitizeGuard.clean(item.original.message)),
            self.vectors,
            self.config.floor,
            self.config.margin,
        )
        if vetoed_by:
            logger.debug("%s: vetoed by benign centroid %r", self.name, vetoed_by)
        return dropped
