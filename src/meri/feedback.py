"""
Reader feedback for title generation.

Glue between Meri and Luotsi: builds a Luotsi client from Meri settings, matches fetched feedback to articles by
URL signature, and shapes the per-article aggregate that reaches the prompt.

Two failure modes, deliberately different. Fetching is fail-soft — a Google Sheets outage must not stop the
pipeline. Construction is fail-hard — a configured embedding model that cannot load is a broken deployment, and
it fails at start, before any LLM spend.

Personal data: feedback lives in memory only. Message text reaches the guardrail chain when an article claims
it, and the per-article prompt, and nowhere else. It is never logged and never persisted to Rahti — only the
entry's `updated` timestamp moves.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple

from niitti import get_logger

from meri.luotsi import Feedback, FeedbackType, Luotsi, LuotsiSettings
from meri.luotsi.cluster import MessageClusterer, MessageGroup

from .abc import ClickbaitScale
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
    level: ClickbaitScale | None = None
    """
    Clickbaitiness the ORIGINAL headline was rated at when this title was published.

    Readers rate a title without being told why it was rewritten, so the level is the missing half of what they
    are reacting to: "three negatives at Moderately Clickbaity" is evidence about the rating, not just the
    wording. Defaults to None for a title whose rows carry no level, or one this scale does not know.
    """


_LEVELS: dict[str, ClickbaitScale] = {
    re.sub(r"[^a-z0-9]", "", level.value.lower()): level for level in ClickbaitScale
}
"""Scale values, folded so spacing and casing in a spreadsheet cell do not matter."""

_LEVELS["notclickbaity"] = ClickbaitScale.NONE
"""The reader widget's wording for the bottom of the scale, which is not the enum's own."""

_SEVERITY: dict[ClickbaitScale, int] = {level: rank for rank, level in enumerate(ClickbaitScale)}
"""Declaration order of the scale, which runs from least to most clickbaity."""


def _parse_level(raw: str | None) -> ClickbaitScale | None:
    """
    Read a `clickbaitLevel` cell as a scale value.

    The column is free text written by the widget, not by this code, so an unknown value is dropped rather than
    guessed at: a wrong level in the prompt is worse than no level.
    """
    if not raw:
        return None

    level = _LEVELS.get(re.sub(r"[^a-z0-9]", "", raw.lower()))
    if level is None:
        logger.debug("Unknown clickbait level in feedback", level=raw)
    return level


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

    Feedback enters raw and is guarded per signature, the first time an article claims it. The corpus grows
    without bound while the articles one run touches do not, so guarding it all up front would make every run
    pay for the whole history; this way it pays only for what it looks at. Every consumer — the regeneration
    gate, the prompt and the Rahti bump — goes through :meth:`find_by_article`, so all three see one guarded
    view and no unguarded reader text escapes.
    """

    def __init__(self, feedback: list[Feedback], guard: Callable[[list[Feedback]], list[Feedback]] | None = None) -> None:
        """
        :param feedback: Raw feedback from every source.
        :param guard: The guardrail chain, run per signature on first match. None leaves feedback unguarded.
        """
        self.map: dict[str, list[Feedback]] = {}
        for item in feedback:
            self.map.setdefault(item.url_sign, []).append(item)

        # Seeded from raw feedback, so a signature no article claims counts as unmatched even when the guards
        # would have emptied it. It means "no article claimed this", not "nothing survived".
        self.unmatched: set[str] = set(self.map)

        self._guard = guard
        self._seen: set[str] = set()
        self.guarded = 0
        """Signatures put through the chain. Final only once every article has been matched."""

        self.dropped = 0
        """Feedback items the chain removed."""

    def find_by_article(self, article: Article) -> list[Feedback]:
        """
        Find every feedback item matching any of the article's URL signatures.

        An article reached by several URLs collects the feedback left on all of them.
        """
        matched: list[Feedback] = []
        for url in article.urls:
            if url.signature in self.map:
                matched.extend(self._sanitized(url.signature))
                self.unmatched.discard(url.signature)
        return matched

    def _sanitized(self, signature: str) -> list[Feedback]:
        """Guard one signature's feedback on first access, then serve it from the map."""
        if self._guard and signature not in self._seen:
            kept = self._guard(self.map[signature])
            self.dropped += len(self.map[signature]) - len(kept)
            self.guarded += 1
            self.map[signature] = kept
            self._seen.add(signature)
        return self.map[signature]


def build_matcher(settings: LuotsiSettings | None) -> FeedbackMatcher:
    """
    Fetch all reader feedback and wrap it in a matcher that guards it per article.

    Fail-soft: an unreachable source must not stop the run. Construction stays outside the `try` so that a
    broken embedding model or a missing guard artifact still fails the run at start — see the module docstring.

    :return: A matcher over the fetched feedback, empty when feedback is disabled or unreachable.
    """
    client = create_luotsi(settings)
    if not client:
        return FeedbackMatcher([])

    try:
        feedback = client.collect()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not fetch reader feedback, continuing without it", error=str(e))
        return FeedbackMatcher([])

    logger.info("Fetched reader feedback", count=len(feedback))
    return FeedbackMatcher(feedback, guard=client.guard)


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
    levels: dict[str | None, ClickbaitScale] = {}
    ranks: dict[str | None, tuple[datetime, int]] = {}

    for item in feedback:
        tallies[item.converted_title][item.type] += 1

        when = item.submitted_at or OLDEST
        newest[item.converted_title] = max(newest.get(item.converted_title, OLDEST), when)

        # The level a title was published at can change between regenerations while the title itself does not,
        # so the newest row that names one wins: it is the rating the most recent readers were reacting to. A row
        # naming none cannot erase it, and two rows stamped alike break the tie on severity — otherwise the same
        # feedback renders with or without a level depending only on the order the source returned it in.
        if level := _parse_level(item.clickbait_level):
            rank = (when, _SEVERITY[level])
            if rank > ranks.get(item.converted_title, (OLDEST, -1)):
                ranks[item.converted_title] = rank
                levels[item.converted_title] = level

    return [
        TitleVotes(
            title=title,
            good=tallies[title][FeedbackType.GOOD],
            bad=tallies[title][FeedbackType.BAD],
            suggestions=tallies[title][FeedbackType.SUGGESTION],
            level=levels.get(title),
        )
        for title in sorted(tallies, key=lambda title: newest[title], reverse=True)
    ]
