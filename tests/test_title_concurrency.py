"""
Concurrency test for sharing one TitlePredictor across the worker threads.

`generate_titles` used to construct a TitlePredictor inside the per-article closure, so every article built its
own generator and HTTP client across MAX_WORKERS threads. Sharing one instance is only safe if a Haystack
pipeline can be run concurrently: `Pipeline.run` does not assign to `self`, and ChatPromptBuilder sets its state
in `__init__`, but neither is a promise. This test is the evidence.

If it ever shows cross-talk, go back to constructing the predictor per article and keep this test as the record.
"""

import json
import time

from haystack.dataclasses import ChatMessage
from test_lautta_resilience import make_discovered_article

from meri.lautta import generate_titles
from meri.pipelines.title import TitlePredictor
from meri.settings.settings import Settings

ARTICLES = 6
LLM = {"name": "Primary", "provider": "openai", "model": "gpt-4o-mini", "api_key": "k"}


def title_reply(marker: str) -> ChatMessage:
    """A minimal but valid ArticleTitleResponse, with the marker carried in the generated title."""
    return ChatMessage.from_assistant(
        json.dumps({
            "original_title": f"Original {marker}",
            "evidence": {"content": "c", "tone": "t", "structure": "s"},
            "original_title_clickbaitiness": "Not Clickbait at all",
            "title": f"Title {marker}",
        })
    )


def test_one_predictor_serves_every_article_without_cross_talk(monkeypatch):
    """
    Each article must get the title generated from its own text, with three threads in flight.

    The stub sleeps inside `run` so the threads genuinely overlap, and answers with a title keyed to the input.
    A shared mutable prompt builder would show up here as an article receiving another article's title.
    """
    monkeypatch.setattr("meri.lautta.settings", Settings(llm=[LLM], MAX_WORKERS=3))

    articles = []
    for index in range(ARTICLES):
        discovered = make_discovered_article(f"https://example.com/art{index}")
        discovered.article.text = f"Body of article {index}. " + discovered.article.text
        articles.append(discovered)

    built: list[str] = []

    def make_pipeline(self, llm):
        built.append(llm.name)

        class StubPipeline:
            def run(self, inputs):
                # The prompt builder is shared per LLM, so render through it: that is what would race.
                rendered = prompt.run(template_variables=inputs["prompt_builder"])["prompt"][0].text
                marker = rendered.split("Body of article ")[1][0]
                time.sleep(0.02)
                return {"llm": {"replies": [title_reply(marker)]}}

        prompt = self._prompt_builder()
        self._prompts[llm.name] = prompt
        return StubPipeline()

    monkeypatch.setattr(TitlePredictor, "_make_pipeline", make_pipeline)
    monkeypatch.setattr("meri.pipelines.common.settings", Settings(llm=[LLM]))

    results = generate_titles(articles)

    assert [result.title.title for result in results] == [f"Title {index}" for index in range(ARTICLES)]
    assert built == ["Primary"], f"the predictor must be built once for the run, not per article: {built}"
