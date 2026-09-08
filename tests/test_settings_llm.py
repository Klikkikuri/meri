"""
Tests for how `llm:` entries are resolved into provider settings.

`LLMSetting` is a discriminated union, so pydantic maps `provider:` to its settings class. These tests hold that
the union reports an unknown provider as usefully as the hand-written check it replaced, and that dropping the
hand-written path did not cost the environment-variable fallbacks.
"""

import pytest
from pydantic import ValidationError

from meri.settings.settings import Settings


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
