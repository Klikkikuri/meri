"""
Tests for the guards over a generated headline, and the revision turn they trigger.

Nothing here reaches an LLM: `_make_pipeline` is the seam, and the stub records every rendered message so a
test can see the conversation the model would. The embedder is `stub_embed` from the injection tests, whose
three axis words let a test state how close a headline is to an article.
"""

import json
from unittest.mock import MagicMock

import pytest
from haystack.dataclasses import ChatMessage
from langdetect.lang_detect_exception import ErrorCode, LangDetectException
from test_luotsi_injection import stub_embed

from meri.abc import ArticleLabels, article_url
from meri.article import Article
from meri.exceptions import HeadlineRejected
from meri.pipelines.title import TitlePredictor, language_issue, measure_drift
from meri.settings.settings import Settings

LLM = {"name": "Primary", "provider": "openai", "model": "gpt-4o-mini", "api_key": "k"}

FINNISH = "Hallitus päätti lisätä rahoitusta peruskouluille ensi vuoden talousarviossa"
ENGLISH = "The government decided to increase funding for primary schools in the budget for next year"


def title_reply(title: str) -> ChatMessage:
    """A minimal but valid ArticleTitleResponse carrying the given headline."""
    return ChatMessage.from_assistant(
        json.dumps({
            "original_title": "Original",
            "evidence": {"content": "c", "tone": "t", "structure": "s"},
            "original_title_clickbaitiness": "Not Clickbait at all",
            "title": title,
        })
    )


def make_article(language: str | None, title: str = "ordinary", text: str = "Article text. " * 10) -> Article:
    return Article(
        urls=[article_url("https://example.com/article")],
        labels=[],
        text=text,
        meta={"title": title, "language": language},
    )


def stub(predictor: TitlePredictor, replies: list) -> tuple[list[list[str]], MagicMock]:
    """
    Replace the seam with a pipeline that answers from `replies`, and record the messages of every call.

    :return: One list of message texts per call, and the mock pipeline.
    """
    rendered: list[list[str]] = []
    pipeline = MagicMock()

    def make(llm):
        prompt = predictor._prompt_builder()
        predictor._prompts[llm.name] = prompt

        def run(inputs):
            rendered.append([message.text or "" for message in prompt.run(**inputs["prompt_builder"])["prompt"]])
            answer = replies.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return {"llm": {"replies": [answer]}}

        pipeline.run.side_effect = run
        return pipeline

    predictor._make_pipeline = make  # type: ignore[method-assign]
    return rendered, pipeline


@pytest.fixture
def settings(monkeypatch):
    """One LLM, no sleeping between attempts, and no embedding model unless a test installs one."""
    monkeypatch.setattr("meri.pipelines.common.settings", Settings(llm=[LLM]))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    monkeypatch.setattr(TitlePredictor, "_embedder", lambda self: None)


@pytest.fixture
def embedder(monkeypatch):
    monkeypatch.setattr(TitlePredictor, "_embedder", lambda self: stub_embed)


# --- Language ------------------------------------------------------------------


def test_a_headline_in_the_expected_language_is_returned_after_one_call(settings):
    predictor = TitlePredictor()
    rendered, pipeline = stub(predictor, [title_reply(FINNISH)])

    result = predictor.run(make_article("fi"))

    assert result.title == FINNISH
    assert pipeline.run.call_count == 1
    assert len(rendered[0]) == 2, "the base conversation is a system prompt and one user message"


def test_a_headline_in_the_wrong_language_is_sent_back_for_revision(settings):
    """
    The correction is a continuation: the model sees its own answer, then the request to fix it.

    Not a fresh attempt with a hint, so the reasoning it already did stays in context.
    """
    predictor = TitlePredictor()
    rendered, pipeline = stub(predictor, [title_reply(ENGLISH), title_reply(FINNISH)])

    result = predictor.run(make_article("fi"))

    assert result.title == FINNISH
    assert pipeline.run.call_count == 2
    _system, _user, assistant, revision = rendered[1]
    assert json.loads(assistant)["title"] == ENGLISH
    assert ENGLISH in revision
    assert "'fi'" in revision
    assert "original headline" not in revision, "only the failing guard is mentioned"


def test_a_headline_still_in_the_wrong_language_after_revision_is_rejected(settings):
    """
    One revision turn, then a processing failure the caller stores.

    Retrying next run was rejected on purpose: the model's answer is near-deterministic, so it would cost the
    same two calls per run for the same rejection.
    """
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply(ENGLISH), title_reply(ENGLISH)])

    with pytest.raises(HeadlineRejected, match="language") as rejection:
        predictor.run(make_article("fi"))

    assert rejection.value.label is ArticleLabels.PROCESSING_FAILURE_HEADLINE_LANGUAGE
    assert pipeline.run.call_count == 2


def test_a_regional_language_code_matches_its_base_language(settings):
    """A source may configure `fi-FI`; the extractor detects `fi`. Both mean Finnish."""
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply(FINNISH)])

    predictor.run(make_article("fi-FI"))

    assert pipeline.run.call_count == 1
    assert language_issue(FINNISH, "fi-FI") is None


def test_an_article_without_a_language_skips_the_language_guard(settings):
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply(ENGLISH)])

    assert predictor.run(make_article(None)).title == ENGLISH
    assert pipeline.run.call_count == 1


def test_an_undetectable_headline_passes_the_language_guard(settings, monkeypatch):
    """Text with nothing to detect from is not evidence of the wrong language."""

    def undetectable(_text):
        raise LangDetectException(ErrorCode.CantDetectError, "no features")

    monkeypatch.setattr("meri.pipelines.title.detect_languages", undetectable)
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply("12345")])

    predictor.run(make_article("fi"))

    assert pipeline.run.call_count == 1


def test_the_expected_language_passes_above_the_floor_even_when_not_first(monkeypatch):
    """
    Measured on live headlines: a Finnish headline of foreign names read as Estonian first, Finnish at 0.29.

    An English headline gives Finnish no probability at all, so the floor rescues that case without letting a
    wrong language through.
    """
    monkeypatch.setattr("meri.pipelines.title.detect_languages", lambda _text: {"et": 0.6, "fi": 0.3})
    assert language_issue("whatever", "fi") is None

    monkeypatch.setattr("meri.pipelines.title.detect_languages", lambda _text: {"en": 1.0})
    assert language_issue("whatever", "fi") == {"detected": "en", "expected": "fi"}


def test_a_language_langdetect_confuses_with_the_expected_one_passes(monkeypatch):
    """
    A Swedish sports headline of club names and a score read as Danish with Swedish at zero, and was rejected.

    The model writing Danish for a Swedish article is far less likely than langdetect confusing the two, so the
    guard asks for the right family. A language outside it still fails.
    """
    monkeypatch.setattr("meri.pipelines.title.detect_languages", lambda _text: {"da": 1.0})
    assert language_issue("HIFK leder FM-ligan efter 36–23-vinst mot GrIFK", "sv") is None

    monkeypatch.setattr("meri.pipelines.title.detect_languages", lambda _text: {"et": 0.86, "fi": 0.14})
    assert language_issue("Maailmanmestari John Klingberg: ura ohi", "fi") is None

    monkeypatch.setattr("meri.pipelines.title.detect_languages", lambda _text: {"en": 1.0})
    assert language_issue("HIFK leads the league after a win", "sv") == {"detected": "en", "expected": "sv"}


def test_a_rejection_names_the_headline_it_rejected(settings):
    """The rejected headline is otherwise never stored or printed, and it is what an operator needs to see."""
    predictor = TitlePredictor()
    stub(predictor, [title_reply(ENGLISH), title_reply(ENGLISH)])

    with pytest.raises(HeadlineRejected, match=ENGLISH[:30]):
        predictor.run(make_article("fi"))


# --- Drift ---------------------------------------------------------------------


def test_a_headline_that_drifts_from_the_article_is_sent_back_for_revision(settings, embedder):
    """The revision names the original headline as the thing to verify against, and nothing about language."""
    predictor = TitlePredictor()
    rendered, pipeline = stub(predictor, [title_reply("unrelated"), title_reply("ordinary")])

    result = predictor.run(make_article(None, title="ordinary", text="ordinary " * 20))

    assert result.title == "ordinary"
    assert pipeline.run.call_count == 2
    revision = rendered[1][-1]
    assert "original headline" in revision
    assert "written in" not in revision


def test_a_headline_that_still_drifts_after_revision_is_accepted(settings, embedder):
    """Drift is a heuristic, so a second miss is logged and published rather than blocking the article."""
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply("unrelated"), title_reply("unrelated")])

    result = predictor.run(make_article(None, title="ordinary", text="ordinary " * 20))

    assert result.title == "unrelated"
    assert pipeline.run.call_count == 2


def test_the_drift_guard_is_off_without_an_embedding_model(settings):
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply("unrelated")])

    predictor.run(make_article(None, title="ordinary", text="ordinary " * 20))

    assert pipeline.run.call_count == 1


def test_the_drift_margin_comes_from_the_pipeline_definition(monkeypatch, embedder):
    """`drift_margin` is a pipeline-specific key, so it has to survive settings load and reach the guard."""
    monkeypatch.setattr(
        "meri.pipelines.common.settings", Settings(llm=[LLM], pipelines={"title": {"drift_margin": 2.0}})
    )
    predictor = TitlePredictor()
    _, pipeline = stub(predictor, [title_reply("unrelated")])

    predictor.run(make_article(None, title="ordinary", text="ordinary " * 20))

    assert pipeline.run.call_count == 1, "a margin above the whole similarity range never fires"


def test_a_typo_in_a_title_key_is_caught_at_construction(monkeypatch):
    """The base settings model allows extras; this pipeline's own model is where a typo has to fail."""
    monkeypatch.setattr("meri.pipelines.common.settings", Settings(llm=[LLM], pipelines={"title": {"drift_margn": 0.1}}))

    with pytest.raises(ValueError, match="drift_margn"):
        TitlePredictor().run(make_article(None))


def test_measure_drift_compares_both_headlines_against_the_article():
    drifted = measure_drift(stub_embed, "ordinary ordinary", "ordinary", "unrelated", 0.1)
    assert drifted.drifted
    assert drifted.original == pytest.approx(1.0)
    assert drifted.generated == pytest.approx(0.0)

    assert not measure_drift(stub_embed, "ordinary ordinary", "ordinary", "ordinary", 0.1).drifted


def test_both_guards_failing_produce_one_revision_naming_both(settings, embedder):
    predictor = TitlePredictor()
    rendered, pipeline = stub(predictor, [title_reply(ENGLISH), title_reply("ordinary")])
    article = make_article("fi", title="ordinary", text="ordinary " * 20)

    with pytest.raises(HeadlineRejected):
        # The revised headline is an axis word, so the drift guard passes; the language guard still fails on
        # it, which is the point: the second review runs both guards again.
        predictor.run(article)

    assert pipeline.run.call_count == 2
    revision = rendered[1][-1]
    assert "written in" in revision
    assert "original headline" in revision
