"""Tests for Luotsi message consolidation."""

from datetime import UTC, datetime

import numpy as np
import pytest

from meri.luotsi import Feedback, FeedbackType, LuotsiSettings
from meri.luotsi.cluster import (
    MessageClusterer,
    agglomerate,
    counter_cosine,
    ngram_counts,
)

# Deterministic stand-in vectors: messages sharing a leading tag land on the same axis.
STUB_VECTORS = {
    "a": [1.0, 0.0, 0.0],
    "b": [0.0, 1.0, 0.0],
    "c": [0.0, 0.0, 1.0],
}


def stub_embed(message: str) -> np.ndarray:
    """Embed by the message's first character, so a test can state which messages are alike."""
    return np.array(STUB_VECTORS[message[0]])


def feedback(message: str, minute: int = 0, type: FeedbackType = FeedbackType.BAD) -> Feedback:
    return Feedback(
        type=type,
        message=message,
        url_sign="sign",
        submitted_at=datetime(2026, 7, 14, 9, minute, tzinfo=UTC),
    )


def test_agglomerate_opens_a_group_per_dissimilar_item():
    groups = agglomerate([0, 0, 5, 0], threshold=1.0, similarity=lambda x, y: float(x == y))

    assert groups == [[0, 1, 3], [2]]


def test_clusterer_merges_similar_messages():
    clusterer = MessageClusterer(stub_embed, threshold=0.9)

    groups = clusterer.group([feedback("a first"), feedback("b other"), feedback("a again", minute=5)])

    assert [group.count for group in groups] == [2, 1]
    assert groups[0].representative.message == "a again"


def test_clusterer_representative_is_the_most_recent():
    clusterer = MessageClusterer(stub_embed, threshold=0.9)

    groups = clusterer.group([feedback("a newest", minute=9), feedback("a oldest", minute=1)])

    assert groups[0].representative.message == "a newest"


def test_clusterer_collects_every_verdict_in_a_group():
    clusterer = MessageClusterer(stub_embed, threshold=0.9)

    groups = clusterer.group(
        [feedback("a one", type=FeedbackType.BAD), feedback("a two", type=FeedbackType.SUGGESTION)]
    )

    assert groups[0].types == {FeedbackType.BAD, FeedbackType.SUGGESTION}


def test_clusterer_ignores_empty_messages():
    clusterer = MessageClusterer(stub_embed, threshold=0.9)

    assert clusterer.group([feedback(""), feedback("   ")]) == []


def test_builtin_mode_merges_casing_and_typo_variants():
    """Without a model, character n-gram cosine still catches the same complaint typed twice."""
    clusterer = MessageClusterer(None, threshold=0.8)

    groups = clusterer.group(
        [
            feedback("Otsikko ei vastaa jutun sisältöä lainkaan"),
            feedback("otsikko ei vastaa jutun sisaltöä lainkaan!"),
        ]
    )

    assert [group.count for group in groups] == [2]


def test_builtin_mode_keeps_unrelated_messages_apart():
    clusterer = MessageClusterer(None, threshold=0.8)

    groups = clusterer.group(
        [
            feedback("Otsikko ei vastaa jutun sisältöä lainkaan"),
            feedback("The headline is much better than the original one"),
        ]
    )

    assert [group.count for group in groups] == [1, 1]


def test_ngram_cosine_is_one_for_identical_text():
    assert counter_cosine(ngram_counts("sama teksti"), ngram_counts("SAMA  teksti!")) == pytest.approx(1.0)


def test_from_settings_without_a_model_uses_the_builtin_mode():
    clusterer = MessageClusterer.from_settings(LuotsiSettings())  # Luotsi carries no default model

    assert clusterer.embed is None
