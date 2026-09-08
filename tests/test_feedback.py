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
    feedback_for_article,
    fetch_feedback,
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
) -> Feedback:
    return Feedback(type=type, message=message, url_sign=url_sign, converted_title=title, submitted_at=submitted_at)


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
    assert result.titles[0] == ("Second try", 1, 0, 1)
    assert result.titles[1] == ("First try", 1, 1, 0)


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


# --- Fetching ----------------------------------------------------------------


def test_fetch_feedback_is_empty_when_disabled():
    assert fetch_feedback(None) == []


def test_fetch_feedback_survives_an_unreachable_source():
    """A Sheets outage must not stop the pipeline."""
    client = MagicMock(get_feedback=MagicMock(side_effect=RuntimeError("sheets down")))

    with patch("meri.feedback.create_luotsi", return_value=client):
        assert fetch_feedback(MagicMock()) == []


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
