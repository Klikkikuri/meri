"""
Message consolidation.

Readers complain about the same title in nearly the same words, and a paste campaign says the exact same thing
many times. Grouping those before they reach a prompt keeps the signal and drops the repetition — 50 identical
messages become one line with a count.
"""

import logging
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .abc import Feedback, FeedbackType
from .embeddings import load_embedder
from .settings import LuotsiSettings

logger = logging.getLogger(__name__)

NGRAM_SIZE = 3
"""Character n-gram width for the built-in mode. Three catches typos without matching every word pair."""


@dataclass(frozen=True)
class MessageGroup:
    """One consolidated complaint. A dataclass, not a NamedTuple: `count` would shadow `tuple.count`."""

    representative: Feedback
    """The most recent member. Its message stands for the whole group."""

    count: int
    """How many messages joined the group."""

    types: set[FeedbackType]
    """Every verdict the members carried."""


def agglomerate[T](items: Sequence[T], threshold: float, similarity: Callable[[T, T], float]) -> list[list[int]]:
    """
    Group items greedily by similarity.

    Each item joins the first group whose seed — the item that opened the group — it is similar enough to, and
    opens a new group otherwise. Comparing against the seed rather than any member keeps groups from chaining
    across a long tail of near-misses, and makes the result depend only on the input order.

    :param items: What to group.
    :param threshold: Similarity at or above which an item joins a group.
    :param similarity: Similarity of two items, in 0..1.
    :return: Groups as lists of indices into `items`, in the order the groups opened.
    """
    groups: list[list[int]] = []
    for index, item in enumerate(items):
        for group in groups:
            if similarity(items[group[0]], item) >= threshold:
                group.append(index)
                break
        else:
            groups.append([index])
    return groups


def cosine(left, right) -> float:
    """Cosine similarity of two vectors, clamped to 0..1 — an opposite direction is no more similar than none."""
    denominator = float((left @ left) ** 0.5 * (right @ right) ** 0.5)
    return max(0.0, float(left @ right) / denominator) if denominator else 0.0


def ngram_counts(message: str) -> Counter[str]:
    """
    Reduce a message to character n-gram counts.

    Case-folded, with punctuation dropped and whitespace collapsed, so casing, spacing and stray punctuation do
    not separate two readers saying the same thing. Character n-grams need no model and no language detection, so
    Finnish and English are covered alike.
    """
    normalized = re.sub(r"[^\w\s]", "", message.casefold())
    normalized = " ".join(normalized.split())
    return Counter(normalized[i : i + NGRAM_SIZE] for i in range(len(normalized) - NGRAM_SIZE + 1))


def counter_cosine(left: Counter[str], right: Counter[str]) -> float:
    """Cosine similarity of two n-gram count vectors."""
    denominator = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    if not denominator:
        return 0.0
    shared = left.keys() & right.keys()
    return sum(left[key] * right[key] for key in shared) / denominator


class MessageClusterer:
    """
    Consolidates similar feedback messages into groups.

    Two modes, chosen by whether an embedding model is configured. Without one, character n-gram cosine catches
    exact repeats, typos and paste-campaign variants. With one, static embeddings also merge true rewordings, and
    — in a multilingual vector space — a Finnish and an English version of the same complaint.
    """

    def __init__(self, embed: "Callable[[str], object] | None", threshold: float) -> None:
        """
        :param embed: Embedding callable for the semantic mode, or None for the built-in mode.
        :param threshold: Similarity at or above which two messages join one group.
        """
        self.embed = embed
        self.threshold = threshold

    @classmethod
    def from_settings(cls, settings: LuotsiSettings) -> "MessageClusterer":
        """Build a clusterer from configuration, sharing the process-wide embedding model when one is set."""
        embed = load_embedder(settings.embedding_model) if settings.embedding_model else None
        return cls(embed, settings.clustering.similarity_threshold)

    def group(self, feedbacks: Sequence[Feedback]) -> list[MessageGroup]:
        """
        Consolidate feedback that says the same thing.

        :param feedbacks: Feedback items to group. Items with an empty message are ignored.
        :return: One group per distinct complaint, in the order the groups opened.
        """
        with_message = [feedback for feedback in feedbacks if feedback.message.strip()]
        if not with_message:
            return []

        messages = [feedback.message for feedback in with_message]
        if self.embed:
            groups = agglomerate([self.embed(message) for message in messages], self.threshold, cosine)
        else:
            groups = agglomerate([ngram_counts(message) for message in messages], self.threshold, counter_cosine)

        return [self._to_group([with_message[index] for index in indices]) for indices in groups]

    @staticmethod
    def _to_group(members: list[Feedback]) -> MessageGroup:
        """Build a group from its members, letting the most recent one speak for it."""
        oldest = datetime.min.replace(tzinfo=UTC)
        representative = max(members, key=lambda f: f.submitted_at or oldest)
        return MessageGroup(
            representative=representative,
            count=len(members),
            types={member.type for member in members},
        )
