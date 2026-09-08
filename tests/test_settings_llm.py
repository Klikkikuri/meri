"""
Tests for how `llm:` entries are resolved into provider settings.

`LLMSetting` is a discriminated union, so pydantic maps `provider:` to its settings class. These tests hold that
the union reports an unknown provider as usefully as the hand-written check it replaced, and that dropping the
hand-written path did not cost the environment-variable fallbacks.
"""

import pytest
from pydantic import BaseModel, ValidationError

from meri.settings.settings import Settings


class DummyFormat(BaseModel):
    """Stands in for a pipeline's output model, to see where response_format is routed."""

    title: str


def test_unknown_provider_is_rejected_naming_the_valid_ones():
    """An unknown `provider:` must fail at settings construction, and say what is valid."""
    with pytest.raises(ValidationError) as exc:
        Settings(llm=[{"name": "X", "provider": "bogus", "model": "m"}])

    message = str(exc.value)
    assert "bogus" in message
    for provider in ("openai", "ollama", "gemini", "openrouter"):
        assert provider in message


def test_a_configured_entry_resolves_to_its_provider_class():
    """The union picks the settings class from the tag, with no reflection over subclasses."""
    settings = Settings(llm=[{"name": "G", "provider": "gemini", "model": "gemini-3.1-flash", "api_key": "k"}])

    (llm,) = settings.llm
    assert type(llm).__name__ == "GoogleGeminiSettings"
    assert llm.name == "G"


def test_the_api_key_still_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    """
    Validating through the union must not cost the settings-source fallbacks.

    An operator names the provider in `config.yaml` and keeps the key in the environment, so an entry with no
    `api_key` has to keep working.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "from-the-environment")

    (llm,) = Settings(llm=[{"name": "G", "provider": "gemini", "model": "gemini-3.1-flash"}]).llm

    assert llm.api_key.get_secret_value() == "from-the-environment"  # type: ignore[union-attr]


def test_a_configured_key_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch):
    """Configuration is more specific than the environment, so it must not be overridden by it."""
    monkeypatch.setenv("GEMINI_API_KEY", "from-the-environment")

    (llm,) = Settings(
        llm=[{"name": "G", "provider": "gemini", "model": "gemini-3.1-flash", "api_key": "from-the-config"}]
    ).llm

    assert llm.api_key.get_secret_value() == "from-the-config"  # type: ignore[union-attr]


def test_an_empty_llm_list_still_falls_back_to_detection(monkeypatch: pytest.MonkeyPatch):
    """
    The one job left to the validator: discover a provider when the configuration names none.

    Detection reads every key it knows, and a developer machine has more than one, so this asserts that the
    detected entry is present rather than that it is alone.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-detected")

    detected = Settings(llm=[]).llm

    assert "openai" in {llm.provider for llm in detected}


def test_ollama_is_served_through_the_openai_compatible_api():
    """
    Ollama must build an OpenAI generator pointed at `/v1`, with structured output in generation_kwargs.

    The native endpoint took `response_format` as a constructor argument and accepted only `"json"` or a schema
    dict, so structured output never reached it. This is the regression guard for that.
    """
    from meri.llm import get_generator

    (llm,) = Settings(llm=[{"name": "O", "provider": "ollama", "model": "llama3"}]).llm

    assert str(llm.api_base_url) == "http://ollama:11434/v1"

    generator = get_generator(llm, response_format=DummyFormat)

    assert type(generator).__name__ == "OpenAIChatGenerator"
    assert generator.api_base_url == "http://ollama:11434/v1"
    assert generator.generation_kwargs["response_format"] is DummyFormat


def test_the_old_url_spelling_still_selects_the_base_url():
    """Configurations written before the move say `url:`, and must keep working."""
    (llm,) = Settings(llm=[{"name": "O", "provider": "ollama", "model": "llama3", "url": "http://box:11434/v1"}]).llm

    assert str(llm.api_base_url) == "http://box:11434/v1"


def test_the_native_endpoint_is_rejected_with_the_fix_in_the_message():
    """A `/api` URL answers nothing an OpenAI client asks, so it fails at startup rather than at the first call."""
    with pytest.raises(ValidationError) as exc:
        Settings(llm=[{"name": "O", "provider": "ollama", "model": "llama3", "url": "http://ollama:11434/api"}])

    assert "/v1" in str(exc.value)


def test_detection_points_at_the_openai_compatible_endpoint(monkeypatch: pytest.MonkeyPatch):
    """`OLLAMA_HOST` names the bare host, so detection has to add the `/v1` itself."""
    monkeypatch.setattr("meri.settings.llms._pull_default_ollama_model", lambda _url: "llama3")
    monkeypatch.setenv("OLLAMA_HOST", "http://box:11434")

    detected = {llm.provider: llm for llm in Settings(llm=[]).llm}

    assert str(detected["ollama"].api_base_url) == "http://box:11434/v1"


def test_detection_asks_the_native_api_at_the_same_root(monkeypatch: pytest.MonkeyPatch):
    """An `/api`-suffixed host must not reach model detection unstripped, or it asks for `/api/api/ps`."""
    asked = []
    monkeypatch.setattr("meri.settings.llms._pull_default_ollama_model", lambda url: asked.append(url) or "llama3")
    monkeypatch.setenv("OLLAMA_HOST", "http://box:11434/api/")

    detected = {llm.provider: llm for llm in Settings(llm=[]).llm}

    assert asked == ["http://box:11434"]
    assert str(detected["ollama"].api_base_url) == "http://box:11434/v1"
