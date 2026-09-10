"""
Luotsi client.

Builds the configured feedback sources, collects their items and hands back one combined list.
"""

import logging
from pathlib import Path

from .abc import Feedback, FeedbackItem, FeedbackSource
from .embeddings import load_embedder
from .guards import build_guards
from .settings import InjectionConfig, LuotsiSettings
from .settings.source import Csv, GoogleSheets
from .sources import CsvFeedbackSource, SheetsFeedbackSource

logger = logging.getLogger(__name__)


def model_identity(settings: LuotsiSettings) -> str | None:
    """
    What to record in the guard's artifact as the model that produced it.

    The host's `embedding_model_id` when it supplied one, otherwise the model directory's name.

    That fallback is a known blind spot, and the reason the host is asked for something better: a bare
    directory name cannot tell `minishlab/potion-multilingual-128M` from `otherorg/potion-multilingual-128M`,
    and neither can the dimension check, so the guard would score one model's centroids against another's
    embeddings and fail open. A qualified identifier closes that. Neither closes the remaining case — new
    weights fetched into the SAME directory under the same identifier — which only fingerprinting what the
    model produces would catch.
    """
    if settings.embedding_model_id:
        return settings.embedding_model_id
    return settings.embedding_model.name if settings.embedding_model else None


def provision(settings: LuotsiSettings, *, force: bool = False) -> Path | None:
    """
    Bring the artifacts this configuration needs up to date. WRITES; call it deliberately.

    The counterpart to :func:`~meri.luotsi.embeddings.download_model`: guards read what a host has provisioned, and
    a host provisions at a point of its choosing — before any spend, once per run — rather than having a
    constructor decide for it. A host that skips this does not get stale vectors; it gets a guard that refuses
    to build and says why.

    Does nothing when the chain runs no injection guard, or when there is no embedding model to train with.

    :param settings: The feedback configuration.
    :param force: Rebuild the artifact even when it reads as current. For the one staleness the recorded
        identity cannot see: the same model identifier re-fetched, whose new weights the old centroids do not
        belong to.
    :raises ValueError: When the exemplars cannot produce a classifier.
    :raises OSError: When the artifact cannot be written.
    :return: The artifact path, or None when there was nothing to provision.
    """
    from .guards import DEFAULT_CHAIN
    from .guards.provision import artifact_path, ensure_guard_vectors

    chain = settings.guardrails if settings.guardrails is not None else DEFAULT_CHAIN
    config = next((guard for guard in chain if isinstance(guard, InjectionConfig)), None)
    if config is None or settings.embedding_model is None:
        return None

    path = artifact_path(config, settings.guard_vectors)
    _, reason = ensure_guard_vectors(
        config, load_embedder(settings.embedding_model), model_identity(settings), path, force=force
    )
    if reason:
        logger.info("Provisioned guard vectors at %s: %s", path, reason)
    return path


class Luotsi:
    """Collects reader feedback from every configured source and sanitizes it before anyone else sees it."""

    def __init__(self, settings: LuotsiSettings) -> None:
        """
        :param settings: Luotsi configuration.
        """
        self.settings = settings
        self.sources = [self._build_source(config) for config in settings.sources]
        # A configured model is a hard requirement: load errors propagate rather than degrade the guard silently.
        embed = load_embedder(settings.embedding_model) if settings.embedding_model else None
        model_name = model_identity(settings)
        self.guards = build_guards(
            settings.guardrails, embed=embed, model_name=model_name, vectors=settings.guard_vectors
        )
        logger.info(
            "Initialized Luotsi with %d feedback source(s) and %d guard(s)", len(self.sources), len(self.guards)
        )

    @staticmethod
    def _build_source(config: Csv | GoogleSheets) -> FeedbackSource:
        """Instantiate the source class matching a source configuration."""
        match config:
            case Csv():
                return CsvFeedbackSource(config)
            case GoogleSheets():
                return SheetsFeedbackSource(config)

    def collect(self) -> list[Feedback]:
        """
        Fetch feedback from every source, unguarded.

        One failing source does not stop the others.

        :return: Raw feedback items, exactly as the sources produced them.
        """
        feedbacks: list[Feedback] = []
        for source in self.sources:
            try:
                feedbacks.extend(source.get_feedback())
            except Exception as e:  # noqa: BLE001
                logger.error("Error fetching feedback from %s: %s", type(source).__name__, e)

        return feedbacks

    def guard(self, feedback: list[Feedback]) -> list[Feedback]:
        """
        Run the guardrail chain over one batch.

        Every guard is per-item and holds no cross-item state, so guarding a subset gives those items the same
        verdict as guarding the whole corpus. A caller may therefore guard lazily, batch by batch, rather than
        all at once. The one exception is a guard that raises: it is skipped for the batch it was handed, so a
        fault narrows to that batch instead of the corpus.

        Does not raise: one guard failing does not discard the batch.

        :param feedback: Items to sanitize.
        :return: The items that survived, in their processed form.
        """
        items = [FeedbackItem(original=item, processed=item) for item in feedback]
        for guard in self.guards:
            try:
                items = guard.run(items)
            except Exception as e:  # noqa: BLE001
                logger.error("Guard %r failed and was skipped: %s", guard.name, e)

        return [item.processed for item in items]

    def get_feedback(self) -> list[Feedback]:
        """
        Collect feedback from every source and run it through the guardrail chain.

        The one-shot form. A caller that only needs part of the corpus guards lazily instead, with
        :meth:`collect` and :meth:`guard`.

        :return: Sanitized feedback items.
        """
        return self.guard(self.collect())
