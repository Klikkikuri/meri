from unittest.mock import MagicMock, patch

import pytest
from haystack.core.errors import PipelineRuntimeError
from haystack.dataclasses import ChatMessage
from pydantic import BaseModel

from meri.pipelines.common import StructuredPipeline


class DummyOutput(BaseModel):
    title: str


class DummyPipeline(StructuredPipeline):
    output_model = DummyOutput
    PIPELINE_NAME = "test_retry"


def test_pipeline_retry_success_on_first_try():
    """Verify pipeline returns result immediately when run succeeds."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_reply = ChatMessage.from_assistant('{"title": "Hello World"}', meta={"model": "test-model"})
    mock_pipeline.run.return_value = {"llm": {"replies": [mock_reply]}}
    dummy.pipeline = mock_pipeline
    dummy._prompt = MagicMock(variables=["var1"])

    res = dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert isinstance(res, DummyOutput)
    assert res.title == "Hello World"
    assert mock_pipeline.run.call_count == 1


def test_pipeline_retry_recovers_after_failure():
    """Verify pipeline retries upon failure and succeeds on subsequent attempt."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()

    bad_error = PipelineRuntimeError("llm", object, "LLM validation failed")
    valid_reply = ChatMessage.from_assistant('{"title": "Recovered Title"}', meta={"model": "test-model"})

    mock_pipeline.run.side_effect = [
        bad_error,
        {"llm": {"replies": [valid_reply]}},
    ]
    dummy.pipeline = mock_pipeline
    dummy._prompt = MagicMock(variables=["var1"])

    with patch("time.sleep") as mock_sleep:
        res = dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert isinstance(res, DummyOutput)
    assert res.title == "Recovered Title"
    assert mock_pipeline.run.call_count == 2
    assert mock_sleep.call_count == 1


def test_pipeline_retry_exhausted_raises():
    """Verify pipeline raises exception after all retry attempts are exhausted."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()

    bad_error = PipelineRuntimeError("llm", object, "Persistent LLM error")
    mock_pipeline.run.side_effect = bad_error
    dummy.pipeline = mock_pipeline
    dummy._prompt = MagicMock(variables=["var1"])

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(PipelineRuntimeError, match="Persistent LLM error"):
            dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert mock_pipeline.run.call_count == 3
    assert mock_sleep.call_count == 2
