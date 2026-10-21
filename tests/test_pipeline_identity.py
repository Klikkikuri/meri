"""
Tests for the identity that a pipeline sends to the LLM provider.

OpenCode Go reads a stable conversation ID from `x-opencode-session`, and asks clients to send their own user
agent. Nothing here reaches an LLM: `_make_pipeline` is the seam, and every test replaces it.
"""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from haystack import Pipeline
from haystack.components.generators.chat.openai import OpenAIChatGenerator
from haystack.dataclasses import ChatMessage
from haystack.utils.auth import Secret
from pydantic import BaseModel

from meri.abc import article_url
from meri.article import Article
from meri.pipelines.common import StructuredPipeline
from meri.pipelines.title import TitlePredictor
from meri.settings.settings import Settings

OPENCODE = {"name": "Go", "provider": "opencode", "model": "deepseek-v4.1-flash", "api_key": "k"}
OPENAI = {"name": "OpenAI", "provider": "openai", "model": "gpt-4o-mini", "api_key": "k"}


class DummyOutput(BaseModel):
    title: str


class DummyPipeline(StructuredPipeline):
    output_model = DummyOutput
    PIPELINE_NAME = "test_identity"


def reply(title: str) -> ChatMessage:
    return ChatMessage.from_assistant(json.dumps({"title": title}))


def title_reply(title: str) -> ChatMessage:
    """A complete `ArticleTitleResponse`, for the title pipeline."""
    return ChatMessage.from_assistant(
        json.dumps(
            {
                "original_title": "Original",
                "evidence": {"content": "c", "tone": "t", "structure": "s"},
                "original_title_clickbaitiness": "Not Clickbait at all",
                "title": title,
            }
        )
    )


def stub(dummy: StructuredPipeline, replies: list) -> list[dict]:
    """
    Replace the `_make_pipeline` seam with a stub that records every pipeline input.

    :param dummy: The pipeline under test.
    :param replies: One chat message or exception per call, in order.
    :return: The recorded inputs, one per call.
    """
    calls: list[dict] = []
    pipeline = MagicMock()

    def make(llm):
        prompt = dummy._prompt_builder()
        dummy._prompts[llm.name] = prompt

        def run(inputs):
            calls.append(inputs)
            answer = replies.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return {"llm": {"replies": [answer]}}

        pipeline.run.side_effect = run
        return pipeline

    dummy._make_pipeline = make  # type: ignore[method-assign]
    return calls


def headers_of(call: dict) -> dict[str, str]:
    return call["llm"]["generation_kwargs"]["extra_headers"]


@pytest.fixture
def opencode(monkeypatch):
    """One OpenCode LLM, with a separate bot and LLM identity."""
    monkeypatch.setattr(
        "meri.pipelines.common.settings",
        Settings(llm=[OPENCODE], BOT_USER_AGENT="Meri-Bot/1.0", LLM_USER_AGENT="Meri-LLM/2.0"),
    )


def test_opencode_requests_name_the_client_and_the_conversation(opencode):
    """The provider reads both headers on every request."""
    dummy = DummyPipeline()
    calls = stub(dummy, replies=[reply("A")])

    dummy.run({"var1": "v"}, max_retries=1)

    headers = headers_of(calls[0])
    assert headers["User-Agent"] == "Meri-LLM/2.0"
    assert headers["x-opencode-session"].startswith("ses_")


def test_the_bot_user_agent_is_used_when_no_llm_identity_is_set(monkeypatch):
    """One identity is enough unless the operator asks for a separate one."""
    monkeypatch.setattr("meri.pipelines.common.settings", Settings(llm=[OPENCODE], BOT_USER_AGENT="Meri-Bot/1.0"))
    dummy = DummyPipeline()
    calls = stub(dummy, replies=[reply("A")])

    dummy.run({}, max_retries=1)

    assert headers_of(calls[0])["User-Agent"] == "Meri-Bot/1.0"


def test_a_retry_stays_in_the_same_conversation(opencode):
    """A retry is the same conversation, so it must reuse the session ID."""
    dummy = DummyPipeline()
    calls = stub(dummy, replies=[RuntimeError("boom"), reply("A")])

    dummy.run({}, max_retries=2, initial_delay=0)

    assert len({headers_of(call)["x-opencode-session"] for call in calls}) == 1


def test_each_run_starts_a_new_conversation(opencode):
    """Two articles are two conversations."""
    dummy = DummyPipeline()
    calls = stub(dummy, replies=[reply("A"), reply("B")])

    dummy.run({}, max_retries=1)
    dummy.run({}, max_retries=1)

    assert headers_of(calls[0])["x-opencode-session"] != headers_of(calls[1])["x-opencode-session"]


def test_the_user_agent_is_sent_to_every_openai_compatible_provider(monkeypatch):
    """A provider without a session header still gets the client identity, but no conversation ID."""
    monkeypatch.setattr(
        "meri.pipelines.common.settings", Settings(llm=[OPENAI], BOT_USER_AGENT="Meri-Bot/1.0")
    )
    dummy = DummyPipeline()
    calls = stub(dummy, replies=[reply("A")])

    dummy.run({}, max_retries=1)

    assert headers_of(calls[0]) == {"User-Agent": "Meri-Bot/1.0"}


def test_a_generator_that_rejects_request_headers_gets_none(monkeypatch):
    """
    OpenRouter sends every extra generation argument in the request body.

    An `extra_headers` argument would become an unknown body field, so OpenRouter must not receive the input.
    """
    monkeypatch.setattr(
        "meri.pipelines.common.settings",
        Settings(llm=[{"name": "OpenRouter", "provider": "openrouter", "api_key": "k"}]),
    )
    dummy = DummyPipeline()
    calls = stub(dummy, replies=[reply("A")])

    dummy.run({}, max_retries=1)

    assert "llm" not in calls[0]


def test_the_revision_turn_reuses_the_session_of_the_first_turn(opencode, monkeypatch):
    """The correction turn continues the first answer, so the provider sees one conversation."""
    predictor = TitlePredictor()
    calls = stub(predictor, replies=[title_reply("First"), title_reply("Revised")])
    reviews = iter([{"language": {"detected": "en", "expected": "fi"}}, None])
    monkeypatch.setattr(predictor, "_review", lambda _result, _article: next(reviews))
    article = Article(
        urls=[article_url("https://example.com/article")],
        labels=[],
        text="Article text. " * 10,
        meta={"title": "ordinary", "language": "fi"},
        created_at=None,
        updated_at=None,
    )

    predictor.run(article)

    assert len(calls) == 2
    assert headers_of(calls[0])["x-opencode-session"] == headers_of(calls[1])["x-opencode-session"]


def test_the_openai_generator_sends_extra_headers_as_http_headers():
    """The mechanism this design relies on: the SDK consumes `extra_headers`, it does not put it in the body."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    generator = OpenAIChatGenerator(
        api_key=Secret.from_token("k"),
        model="m",
        api_base_url="https://opencode.ai/inference/go/openai/v1",
        http_client_kwargs={"transport": httpx.MockTransport(handler)},
    )
    pipeline = Pipeline()
    pipeline.add_component("llm", generator)

    pipeline.run(
        {
            "llm": {
                "messages": [ChatMessage.from_user("hi")],
                "generation_kwargs": {
                    "extra_headers": {"User-Agent": "Meri-LLM/2.0", "x-opencode-session": "ses_test"}
                },
            }
        }
    )

    assert seen["user-agent"] == "Meri-LLM/2.0"
    assert seen["x-opencode-session"] == "ses_test"
