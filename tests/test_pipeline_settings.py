"""
Tests for pipeline definitions in the configuration.

A definition names the LLMs a pipeline may use. The names are keys into `llm:`, so a name that matches nothing
has to fail at settings construction, which is where `bootstrap.setup()` builds them: a bad configuration stops
the process instead of failing at the first generation.
"""

import pytest
from pydantic import ValidationError

from meri.settings.pipelines import PipelineSettings
from meri.settings.settings import Settings

PRIMARY = {"name": "Primary", "provider": "openai", "model": "gpt-4o-mini", "api_key": "k1"}
BACKUP = {"name": "Backup", "provider": "openai", "model": "gpt-4o", "api_key": "k2"}


def test_a_config_with_no_pipelines_key_validates_to_an_empty_mapping():
    """Definitions are optional. Absence must mean defaults, not a failure."""
    assert Settings(llm=[PRIMARY]).pipelines == {}


def test_a_definition_is_read_into_its_settings_model():
    """The mapping is keyed by pipeline name, so duplicates are impossible by construction."""
    settings = Settings(llm=[PRIMARY, BACKUP], pipelines={"title": {"llm": ["Backup"], "max_retries": 5}})

    definition = settings.pipelines["title"]
    assert definition.llm == ["Backup"]
    assert definition.max_retries == 5
    assert definition.backoff_factor == 2.0


def test_an_unknown_llm_name_is_rejected_naming_the_offender_and_the_valid_ones():
    """The message has to carry both halves, or the operator cannot tell what to write instead."""
    # Pydantic wraps a validator's ValueError, so UnknownLLMError reaches the operator inside a ValidationError.
    with pytest.raises(ValidationError) as exc:
        Settings(llm=[PRIMARY], pipelines={"title": {"llm": ["Typo"]}})

    message = str(exc.value)
    assert "title" in message
    assert "Typo" in message
    assert "Primary" in message


def test_duplicate_llm_names_are_rejected():
    """Names are keys now, so a collision must not resolve silently to whichever came first."""
    with pytest.raises(ValidationError, match="Duplicate"):
        Settings(llm=[PRIMARY, dict(BACKUP, name="Primary")])


def test_pipeline_specific_keys_survive_settings_load():
    """
    Settings cannot know what a pipeline declares, so it must carry unknown keys rather than drop them.

    The pipeline re-validates them against its own model, which is where a typo is caught.
    """
    settings = Settings(llm=[PRIMARY], pipelines={"feedback_translate": {"batch_size": 5}})

    assert settings.pipelines["feedback_translate"].model_extra == {"batch_size": 5}


def test_a_subclass_forbids_the_extras_the_base_allows():
    """
    The base allows extras so load succeeds; a pipeline's own model forbids them so a typo is loud.

    Without this the "caught at pipeline construction" promise is empty.
    """

    class TranslateSettings(PipelineSettings):
        model_config = PipelineSettings.model_config | {"extra": "forbid"}

        batch_size: int = 20

    assert TranslateSettings.model_validate({"batch_size": 5}).batch_size == 5

    with pytest.raises(ValueError, match="batch_sise"):
        TranslateSettings.model_validate({"batch_sise": 5})
