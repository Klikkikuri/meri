"""
Luotsi client.

Builds the configured feedback sources, collects their items and hands back one combined list.
"""

import logging

from .abc import Feedback, FeedbackItem, FeedbackSource
from .embeddings import load_embedder
from .guards import build_guards
from .settings import LuotsiSettings
from .settings.source import Csv, GoogleSheets
from .sources import CsvFeedbackSource, SheetsFeedbackSource

logger = logging.getLogger(__name__)


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
        model_name = settings.embedding_model.name if settings.embedding_model else None
        self.guards = build_guards(settings.guardrails, embed=embed, model_name=model_name)
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
