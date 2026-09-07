"""
Reader feedback for title generation.

Glue between Meri and Luotsi: builds a Luotsi client from Meri settings, matches fetched feedback to articles by
URL signature, and shapes the per-article aggregate that reaches the prompt.

Two failure modes, deliberately different. Fetching is fail-soft — a Google Sheets outage must not stop the
pipeline. Construction is fail-hard — a configured embedding model that cannot load is a broken deployment, and
it fails at start, before any LLM spend.

Personal data: feedback lives in memory only. Message text reaches the guardrail chain and the per-article
prompt, and nowhere else. It is never logged and never persisted to Rahti — only the entry's `updated` timestamp
moves.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import NamedTuple

from luotsi.cluster import MessageClusterer, MessageGroup
from niitti import get_logger

from luotsi import Feedback, FeedbackType, Luotsi, LuotsiSettings

from .article import Article

logger = get_logger(__name__)

OLDEST = datetime.min.replace(tzinfo=UTC)
"""Sort key for feedback whose timestamp could not be parsed. Comparing against a naive minimum would raise."""

ACTIONABLE = {FeedbackType.BAD, FeedbackType.SUGGESTION}
"""Verdicts that justify regenerating a title. Praise is signal for the prompt, but nothing to act on."""


class TitleVotes(NamedTuple):
    """How readers received one previously generated title."""

    title: str | None
    good: int
    bad: int
    suggestions: int


class ArticleFeedback(NamedTuple):
    """Everything readers said about one article."""

    titles: list[TitleVotes]
    """Vote history per generated title, newest first. Never capped or clustered — the counts stay exact."""

    items: list[MessageGroup]
    """Consolidated message groups, most recent first, capped."""


def create_luotsi(settings: LuotsiSettings | None) -> Luotsi | None:
    """
    Build a Luotsi client, or None when feedback is not configured.

    :param settings: The `luotsi` section of Meri's settings.
    :raises Exception: When a configured embedding model or guard artifact cannot load.
    """
    return Luotsi(settings) if settings else None


def fetch_feedback(settings: LuotsiSettings | None) -> list[Feedback]:
    """
    Fetch all reader feedback.

    Fail-soft: an unreachable source must not stop the run. Construction failures still propagate — see the
    module docstring.

    :return: Sanitized feedback items, or an empty list when disabled or unreachable.
    """
    client = create_luotsi(settings)
    if not client:
        return []

    try:
        feedback = client.get_feedback()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not fetch reader feedback, continuing without it", error=str(e))
        return []

    logger.info("Fetched reader feedback", count=len(feedback))
    return feedback


def newest_actionable(feedback: list[Feedback]) -> datetime | None:
    """
    Time of the most recent feedback that justifies a regeneration.

    Fail-closed: an item with no parseable timestamp never triggers one, because it cannot be compared against
    the stored entry and would otherwise re-trigger on every run.
    """
    times = [item.submitted_at for item in feedback if item.type in ACTIONABLE and item.submitted_at]
    return max(times) if times else None


class FeedbackMatcher:
    """
    Matches feedback to articles by URL signature.

    Builds the lookup once, so one pass over the feedback serves every article.
    """

    def __init__(self, feedback: list[Feedback]) -> None:
        self.map: dict[str, list[Feedback]] = {}
        for item in feedback:
            self.map.setdefault(item.url_sign, []).append(item)

        self.unmatched: set[str] = set(self.map)

    def find_by_article(self, article: Article) -> list[Feedback]:
        """
        Find every feedback item matching any of the article's URL signatures.

        An article reached by several URLs collects the feedback left on all of them.
        """
        matched: list[Feedback] = []
        for url in article.urls:
            if url.signature in self.map:
                matched.extend(self.map[url.signature])
                self.unmatched.discard(url.signature)
        return matched


def feedback_for_article(
    matcher: FeedbackMatcher, article: Article, clusterer: MessageClusterer, limit: int
) -> ArticleFeedback | None:
    """
    Build the per-article aggregate the prompt renders.

    :param matcher: The matcher holding this run's feedback.
    :param article: The article to gather feedback for.
    :param clusterer: Consolidates messages that say the same thing.
    :param limit: Most message groups to keep.
    :return: The aggregate, or None when the article has no feedback.
    """
    feedback = matcher.find_by_article(article)
    if not feedback:
        return None

    groups = sorted(
        clusterer.group(feedback),
        key=lambda group: group.representative.submitted_at or OLDEST,
        reverse=True,
    )

    return ArticleFeedback(titles=_tally_titles(feedback), items=groups[:limit])


def _tally_titles(feedback: list[Feedback]) -> list[TitleVotes]:
    """
    Score every title readers have seen for this article, newest first.

    Across regenerations, different generated titles collect votes. The whole scoreboard goes to the model, so it
    can reinforce the qualities of titles that worked and avoid repeating ones that did not. Positives count here
    even though they never trigger a regeneration on their own.
    """
    tallies: dict[str | None, Counter[FeedbackType]] = defaultdict(Counter)
    newest: dict[str | None, datetime] = {}

    for item in feedback:
        tallies[item.converted_title][item.type] += 1
        newest[item.converted_title] = max(newest.get(item.converted_title, OLDEST), item.submitted_at or OLDEST)

    return [
        TitleVotes(
            title=title,
            good=tallies[title][FeedbackType.GOOD],
            bad=tallies[title][FeedbackType.BAD],
            suggestions=tallies[title][FeedbackType.SUGGESTION],
        )
        for title in sorted(tallies, key=lambda title: newest[title], reverse=True)
    ]
