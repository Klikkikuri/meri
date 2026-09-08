"""Tests for :mod:`meri.feedback` — matching reader feedback to articles and shaping it for the prompt."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from luotsi.cluster import MessageClusterer, MessageGroup
from pydantic import AnyHttpUrl

from luotsi import Feedback, FeedbackType
from meri.abc import ArticleUrl, ClickbaitScale
from meri.article import Article
from meri.feedback import (
    ArticleFeedback,
    FeedbackMatcher,
    TitleVotes,
    build_matcher,
    feedback_for_article,
    newest_actionable,
)
from meri.lautta import RahtiCleaner
from meri.rahti import RahtiData, RahtiEntry, RahtiUrl

NOON = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def make_feedback(
    url_sign: str,
    message: str = "test",
    type: FeedbackType = FeedbackType.GOOD,
    title: str | None = "Generated title",
    submitted_at: datetime | None = NOON,
    clickbait_level: str | None = None,
) -> Feedback:
    return Feedback(
        type=type,
        message=message,
        url_sign=url_sign,
        converted_title=title,
        submitted_at=submitted_at,
        clickbait_level=clickbait_level,
    )


def make_article(*hrefs: str) -> Article:
    return Article(urls=[ArticleUrl(href=AnyHttpUrl(href)) for href in hrefs])


def make_cleaner(entry_updated: datetime) -> tuple[RahtiCleaner, Article]:
    """A cleaner holding one entry for one article, both stamped at `entry_updated`."""
    article = Article(
        urls=[ArticleUrl(href=AnyHttpUrl("https://example.com/article-1"))], created_at=NOON, updated_at=NOON
    )
    entry = RahtiEntry(
        updated=entry_updated,
        urls=[RahtiUrl(sign=article.urls[0].signature, labels=[])],
        title="Existing title",
        clickbaitiness=ClickbaitScale.NONE,
        labels=[],
        outlet="example",
    )
    rahti = RahtiData(status="ok", schema_version="0.1.0", updated=entry_updated, entries=[entry])
    return RahtiCleaner(rahti), article


@pytest.fixture
def clusterer() -> MessageClusterer:
    """Built-in mode, so no test needs the embedding model."""
    return MessageClusterer(None, threshold=0.8)


hash_urls = patch("meri.abc.hash_url", side_effect=lambda url: f"sig::{url}")


# --- Matching ----------------------------------------------------------------


@hash_urls
def test_find_by_article_matches_by_signature(_hash):
    article = make_article("https://example.com/article-1")
    feedback = make_feedback(article.urls[0].signature)

    assert FeedbackMatcher([feedback]).find_by_article(article) == [feedback]


@hash_urls
def test_find_by_article_unions_across_every_url(_hash):
    """An article reached by several URLs collects the feedback left on all of them."""
    article = make_article("https://example.com/a", "https://example.com/b")
    first = make_feedback(article.urls[0].signature, message="from a")
    second = make_feedback(article.urls[1].signature, message="from b")

    assert FeedbackMatcher([first, second]).find_by_article(article) == [first, second]


@hash_urls
def test_find_by_article_without_a_match(_hash):
    article = make_article("https://example.com/unmatched")

    assert FeedbackMatcher([make_feedback("sig::other")]).find_by_article(article) == []


@hash_urls
def test_matcher_tracks_unmatched_signatures(_hash):
    article = make_article("https://example.com/a")
    matcher = FeedbackMatcher([make_feedback(article.urls[0].signature), make_feedback("sig::orphan")])

    matcher.find_by_article(article)

    assert matcher.unmatched == {"sig::orphan"}


# --- Regeneration trigger ----------------------------------------------------


def test_newest_actionable_ignores_positive_feedback():
    """Praise is signal for the prompt, but nothing to act on."""
    assert newest_actionable([make_feedback("s", type=FeedbackType.GOOD)]) is None


def test_newest_actionable_ignores_an_unparseable_timestamp():
    """Fail closed: a feedback item that cannot be compared would re-trigger on every run."""
    assert newest_actionable([make_feedback("s", type=FeedbackType.BAD, submitted_at=None)]) is None


def test_newest_actionable_takes_the_latest():
    later = NOON.replace(hour=15)
    feedback = [
        make_feedback("s", type=FeedbackType.BAD),
        make_feedback("s", type=FeedbackType.SUGGESTION, submitted_at=later),
    ]

    assert newest_actionable(feedback) == later


@hash_urls
def test_needs_updating_when_feedback_is_newer(_hash):
    cleaner, article = make_cleaner(NOON)
    feedback = make_feedback(article.urls[0].signature, type=FeedbackType.BAD, submitted_at=NOON.replace(hour=13))

    assert cleaner.needs_updating(article, [feedback]) is True


@hash_urls
def test_needs_updating_is_false_for_feedback_at_the_same_time(_hash):
    """Strictly newer, so a bumped entry does not re-trigger on the feedback that caused it."""
    cleaner, article = make_cleaner(NOON)
    feedback = make_feedback(article.urls[0].signature, type=FeedbackType.BAD, submitted_at=NOON)

    assert cleaner.needs_updating(article, [feedback]) is False


@hash_urls
def test_needs_updating_is_false_for_positive_feedback(_hash):
    cleaner, article = make_cleaner(NOON)
    feedback = make_feedback(article.urls[0].signature, type=FeedbackType.GOOD, submitted_at=NOON.replace(hour=13))

    assert cleaner.needs_updating(article, [feedback]) is False


@hash_urls
def test_needs_updating_is_false_without_a_timestamp(_hash):
    cleaner, article = make_cleaner(NOON)
    feedback = make_feedback(article.urls[0].signature, type=FeedbackType.BAD, submitted_at=None)

    assert cleaner.needs_updating(article, [feedback]) is False


# --- Per-article aggregate ---------------------------------------------------


@hash_urls
def test_feedback_for_article_is_none_without_feedback(_hash, clusterer: MessageClusterer):
    article = make_article("https://example.com/a")

    assert feedback_for_article(FeedbackMatcher([]), article, clusterer, limit=3) is None


@hash_urls
def test_scoreboard_groups_votes_by_generated_title(_hash, clusterer: MessageClusterer):
    article = make_article("https://example.com/a")
    sign = article.urls[0].signature
    feedback = [
        make_feedback(sign, title="First try", type=FeedbackType.BAD, submitted_at=NOON),
        make_feedback(sign, title="First try", type=FeedbackType.GOOD, submitted_at=NOON),
        make_feedback(sign, title="Second try", type=FeedbackType.GOOD, submitted_at=NOON.replace(hour=15)),
        make_feedback(sign, title="Second try", type=FeedbackType.SUGGESTION, submitted_at=NOON.replace(hour=14)),
    ]

    result = feedback_for_article(FeedbackMatcher(feedback), article, clusterer, limit=3)

    assert result is not None
    # Newest-voted title first, and positive votes are tallied even though they never trigger a regeneration.
    assert result.titles[0] == ("Second try", 1, 0, 1, None)
    assert result.titles[1] == ("First try", 1, 1, 0, None)


@hash_urls
def test_scoreboard_is_never_capped(_hash, clusterer: MessageClusterer):
    """The message list is capped; vote counts must stay exact."""
    article = make_article("https://example.com/a")
    sign = article.urls[0].signature
    feedback = [make_feedback(sign, title=f"Title {n}", message="") for n in range(6)]

    result = feedback_for_article(FeedbackMatcher(feedback), article, clusterer, limit=2)

    assert result is not None
    assert len(result.titles) == 6
    assert result.items == []


@hash_urls
def test_empty_messages_are_counted_but_not_quoted(_hash, clusterer: MessageClusterer):
    article = make_article("https://example.com/a")
    sign = article.urls[0].signature
    feedback = [make_feedback(sign, message=""), make_feedback(sign, message="otsikko on harhaanjohtava")]

    result = feedback_for_article(FeedbackMatcher(feedback), article, clusterer, limit=3)

    assert result is not None
    assert result.titles[0].good == 2
    assert [group.representative.message for group in result.items] == ["otsikko on harhaanjohtava"]


@hash_urls
def test_repeated_messages_collapse_into_one_group(_hash, clusterer: MessageClusterer):
    """A paste campaign becomes one line with a count, not fifty lines."""
    article = make_article("https://example.com/a")
    sign = article.urls[0].signature
    feedback = [make_feedback(sign, message="Otsikko ei vastaa jutun sisältöä lainkaan") for _ in range(5)]

    result = feedback_for_article(FeedbackMatcher(feedback), article, clusterer, limit=3)

    assert result is not None
    assert [group.count for group in result.items] == [5]


@hash_urls
def test_message_groups_are_capped_newest_first(_hash, clusterer: MessageClusterer):
    article = make_article("https://example.com/a")
    sign = article.urls[0].signature
    feedback = [
        make_feedback(sign, message="Otsikko on aivan liian pitkä tähän tarkoitukseen", submitted_at=NOON),
        make_feedback(sign, message="The headline lost the point of the story", submitted_at=NOON.replace(hour=14)),
        make_feedback(sign, message="Paikkakunnan nimi puuttuu otsikosta kokonaan", submitted_at=NOON.replace(hour=16)),
    ]

    result = feedback_for_article(FeedbackMatcher(feedback), article, clusterer, limit=2)

    assert result is not None
    assert [group.representative.message for group in result.items] == [
        "Paikkakunnan nimi puuttuu otsikosta kokonaan",
        "The headline lost the point of the story",
    ]


# --- Lazy guarding -----------------------------------------------------------


class RecordingGuard:
    """A stand-in for the guardrail chain that remembers every bucket it was handed."""

    def __init__(self, keep: bool = True) -> None:
        self.calls: list[list[Feedback]] = []
        self.keep = keep

    def __call__(self, items: list[Feedback]) -> list[Feedback]:
        self.calls.append(list(items))
        return list(items) if self.keep else []


@hash_urls
def test_feedback_no_article_claims_is_never_guarded(_hash):
    """
    The point of the change: an unbounded corpus must not cost an unbounded amount of guarding.

    Feedback for articles that have aged out of the feeds is never looked at, so it is never embedded and
    never language-detected.
    """
    article = make_article("https://example.com/a")
    guard = RecordingGuard()
    matcher = FeedbackMatcher(
        [make_feedback(article.urls[0].signature), make_feedback("sig::long-forgotten")], guard=guard
    )

    matcher.find_by_article(article)

    assert [item.url_sign for bucket in guard.calls for item in bucket] == [article.urls[0].signature]


@hash_urls
def test_a_signature_is_guarded_only_once(_hash):
    """Three consumers look the same article up per run; re-guarding would re-truncate and re-redact."""
    article = make_article("https://example.com/a")
    guard = RecordingGuard()
    matcher = FeedbackMatcher([make_feedback(article.urls[0].signature)], guard=guard)

    matcher.find_by_article(article)
    matcher.find_by_article(article)

    assert len(guard.calls) == 1
    assert matcher.guarded == 1


@hash_urls
def test_an_article_whose_feedback_is_all_dropped_triggers_no_regeneration(_hash, clusterer: MessageClusterer):
    """
    What guarding at the match point buys: the gate and the prompt agree, so an article whose only feedback
    is an attack never costs an LLM call.
    """
    cleaner, article = make_cleaner(NOON)
    feedback = make_feedback(article.urls[0].signature, type=FeedbackType.BAD, submitted_at=NOON.replace(hour=13))
    matcher = FeedbackMatcher([feedback], guard=RecordingGuard(keep=False))

    matched = matcher.find_by_article(article)

    assert matched == []
    assert feedback_for_article(matcher, article, clusterer, limit=3) is None
    assert cleaner.needs_updating(article, matched) is False
    assert matcher.dropped == 1


@hash_urls
def test_unmatched_counts_a_signature_no_article_claims_even_unguarded(_hash):
    """`unmatched` is seeded from raw feedback, so it means "no article claimed it", not "nothing survived"."""
    article = make_article("https://example.com/a")
    matcher = FeedbackMatcher(
        [make_feedback(article.urls[0].signature), make_feedback("sig::orphan")], guard=RecordingGuard(keep=False)
    )

    matcher.find_by_article(article)

    assert matcher.unmatched == {"sig::orphan"}


# --- Fetching ----------------------------------------------------------------


def test_build_matcher_is_empty_when_disabled():
    assert build_matcher(None).map == {}


def test_build_matcher_survives_an_unreachable_source():
    """A Sheets outage must not stop the pipeline."""
    client = MagicMock(collect=MagicMock(side_effect=RuntimeError("sheets down")))

    with patch("meri.feedback.create_luotsi", return_value=client):
        assert build_matcher(MagicMock()).map == {}


def test_build_matcher_lets_a_broken_deployment_fail():
    """Fail-hard on construction: an embedding model that cannot load must stop the run, not degrade it."""
    with patch("meri.feedback.create_luotsi", side_effect=RuntimeError("no model")), pytest.raises(RuntimeError):
        build_matcher(MagicMock())


def test_build_matcher_wires_the_chain_in_lazily():
    """The matcher must hold the chain, not a pre-guarded list — otherwise nothing is saved."""
    client = MagicMock(collect=MagicMock(return_value=[make_feedback("sig::a")]))

    with patch("meri.feedback.create_luotsi", return_value=client):
        matcher = build_matcher(MagicMock())

    client.guard.assert_not_called()
    assert matcher.map == {"sig::a": [make_feedback("sig::a")]}


# --- Prompt contract ---------------------------------------------------------


def render_feedback_prompt(feedback: ArticleFeedback | None) -> str:
    from haystack.components.builders import PromptBuilder

    from meri.prompts import PROMPT_TEMPLATE_FEEDBACK, get_prompt_template

    builder = PromptBuilder(template=get_prompt_template(PROMPT_TEMPLATE_FEEDBACK), required_variables=[])
    return builder.run(feedback=feedback)["prompt"]


def test_feedback_is_a_registered_prompt_template():
    from meri.pipelines.title import TitlePredictor

    assert "feedback" in TitlePredictor.prompt_templates


def test_feedback_survives_the_prompt_variable_filter():
    """
    `StructuredPipeline.run` drops any kwarg that the assembled prompt does not declare as a variable, silently.
    The template's literal `feedback` reference is what registers it, so guard that here.
    """
    from meri.pipelines.title import TitlePredictor

    assert "feedback" in TitlePredictor()._prompt_builder().variables


def test_prompt_is_empty_without_feedback():
    assert render_feedback_prompt(None).strip() == ""


def test_prompt_renders_the_scoreboard_and_the_untrusted_framing():
    feedback = ArticleFeedback(
        titles=[TitleVotes("Asiantuntijat korostavat kohtuutta", good=2, bad=1, suggestions=1)],
        items=[],
    )

    prompt = render_feedback_prompt(feedback)

    assert '- "Asiantuntijat korostavat kohtuutta": 2 positive, 1 negative, 1 suggestion(s)' in prompt
    assert "UNTRUSTED" in prompt
    assert "<reader_feedback>" in prompt and "</reader_feedback>" in prompt


def test_prompt_escapes_reader_text_and_shows_the_report_count():
    group = MessageGroup(
        representative=Feedback(
            type=FeedbackType.BAD, message="Otsikko on <b>huono</b>", url_sign="s", submitted_at=NOON
        ),
        count=7,
        types={FeedbackType.BAD},
    )
    feedback = ArticleFeedback(titles=[TitleVotes("Otsikko", 0, 7, 0)], items=[group])

    prompt = render_feedback_prompt(feedback)

    assert "<b>" not in prompt
    assert "Otsikko on huono" in prompt
    assert "<reported_times>7</reported_times>" in prompt


# --- The clickbaitiness readers were reacting to --------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Moderately Clickbaity", ClickbaitScale.MODERATE),
        # The widget writes its own wording for the bottom of the scale, not the enum's.
        ("Not Clickbaity", ClickbaitScale.NONE),
        ("Not Clickbait at all", ClickbaitScale.NONE),
        ("  very   clickbaity  ", ClickbaitScale.HIGH),
        # A level this scale does not know is dropped: a wrong level in the prompt is worse than no level.
        ("Somewhat spicy", None),
        ("", None),
        (None, None),
    ],
)
@hash_urls
def test_level_is_read_from_the_widgets_wording(_hash, clusterer: MessageClusterer, raw, expected):
    article = make_article("https://example.com/article-1")
    feedback = [make_feedback("sig::https://example.com/article-1", clickbait_level=raw)]

    result = feedback_for_article(FeedbackMatcher(feedback), article, clusterer, limit=3)

    assert result is not None
    assert result.titles[0].level is expected


@hash_urls
def test_the_newest_row_decides_the_level(_hash, clusterer: MessageClusterer):
    """A title can be republished at a new rating, so the level readers last reacted to is the current one."""
    sign = "sig::https://example.com/article-1"
    feedback = [
        make_feedback(sign, submitted_at=NOON, clickbait_level="Very Clickbaity"),
        make_feedback(sign, submitted_at=NOON.replace(hour=14), clickbait_level="Slightly Clickbaity"),
    ]

    result = feedback_for_article(FeedbackMatcher(feedback), make_article("https://example.com/article-1"), clusterer, 3)

    assert result is not None
    assert result.titles[0].level is ClickbaitScale.LOW


@hash_urls
def test_a_row_without_a_level_does_not_erase_a_known_one(_hash, clusterer: MessageClusterer):
    """Only some rows carry the column, so a blank must not overwrite what an earlier row established."""
    sign = "sig::https://example.com/article-1"
    feedback = [
        make_feedback(sign, submitted_at=NOON, clickbait_level="Extremely Clickbaity"),
        make_feedback(sign, submitted_at=NOON.replace(hour=14), clickbait_level=None),
    ]

    result = feedback_for_article(FeedbackMatcher(feedback), make_article("https://example.com/article-1"), clusterer, 3)

    assert result is not None
    assert result.titles[0].level is ClickbaitScale.EXTREME


def test_prompt_shows_the_level_the_title_was_published_at():
    """Readers never see the rating, so the prompt is where the vote and the rating are put side by side."""
    feedback = ArticleFeedback(
        titles=[TitleVotes("Otsikko", good=0, bad=3, suggestions=0, level=ClickbaitScale.MODERATE)],
        items=[],
    )

    prompt = render_feedback_prompt(feedback)

    assert '- "Otsikko" (original rated Moderately Clickbaity): 0 positive, 3 negative, 0 suggestion(s)' in prompt


def test_prompt_omits_the_level_when_it_is_unknown():
    """Older rows carry no level, and an absent rating must not read as a rating of nothing."""
    feedback = ArticleFeedback(titles=[TitleVotes("Otsikko", 0, 3, 0)], items=[])

    prompt = render_feedback_prompt(feedback)

    assert '- "Otsikko": 0 positive, 3 negative, 0 suggestion(s)' in prompt
    assert "original rated" not in prompt


def test_prompt_permits_revising_the_level_but_not_on_votes_alone():
    """
    The level is the one thing feedback may change, and it is the one unguarded channel.

    Luotsi's guards classify text; a vote carries none, so nothing stops a campaign of negatives. The prompt is
    the only place that can refuse to let counts move the rating, so assert the refusal is actually in it.
    """
    prompt = render_feedback_prompt(ArticleFeedback(titles=[TitleVotes("Otsikko", 0, 3, 0)], items=[]))

    assert "original_title_clickbaitiness" in prompt
    assert "NEVER revise it on vote counts alone" in prompt
    assert "the ARTICLE TEXT bears it out" in prompt


def approved(title: str, count: int = 1) -> MessageGroup:
    """A group of purely positive comments about one generated title."""
    return MessageGroup(
        representative=Feedback(
            type=FeedbackType.GOOD, message="loistava otsikko, kiitos", url_sign="s", converted_title=title
        ),
        count=count,
        types={FeedbackType.GOOD},
    )


def test_prompt_replaces_praise_text_with_the_title_it_approved():
    """Praise reports that a wording worked, so the wording is the useful part of it, not the compliment."""
    feedback = ArticleFeedback(titles=[TitleVotes("Otsikko", 3, 0, 0)], items=[approved("Otsikko", count=3)])

    prompt = render_feedback_prompt(feedback)

    assert "<approved_title>Otsikko</approved_title>" in prompt
    assert "loistava otsikko" not in prompt
    assert "<reported_times>3</reported_times>" in prompt


def test_prompt_keeps_the_text_when_a_group_is_not_purely_positive():
    """A reader who approves AND suggests is asking for something, and the ask is in the words."""
    group = MessageGroup(
        representative=Feedback(
            type=FeedbackType.GOOD, message="good but the year is missing", url_sign="s", converted_title="Otsikko"
        ),
        count=2,
        types={FeedbackType.GOOD, FeedbackType.SUGGESTION},
    )

    prompt = render_feedback_prompt(ArticleFeedback(titles=[TitleVotes("Otsikko", 2, 0, 1)], items=[group]))

    assert "good but the year is missing" in prompt
    assert "<approved_title>" not in prompt


def test_prompt_handles_an_approved_title_that_was_never_recorded():
    feedback = ArticleFeedback(titles=[TitleVotes(None, 1, 0, 0)], items=[approved(None)])  # type: ignore[arg-type]

    assert "<approved_title>(title not recorded)</approved_title>" in render_feedback_prompt(feedback)


def test_prompt_says_the_feedback_comes_from_several_conflicting_people():
    """
    Without this the model reads the block as one voice and tries to satisfy every line at once.

    Readers write independently and cannot see each other, so contradiction is the normal case rather than a
    fault in the data, and `reported_times` is the only figure that means a view is actually shared.
    """
    prompt = render_feedback_prompt(ArticleFeedback(titles=[TitleVotes("Otsikko", 1, 1, 0)], items=[]))

    assert "several different people, not one voice" in prompt
    assert "Conflict here is normal" in prompt
    assert "Do not try to satisfy every comment" in prompt
    assert "One well-argued objection can outweigh several vague approvals" in prompt
    assert "`reported_times` is the only number that says a view is shared" in prompt
