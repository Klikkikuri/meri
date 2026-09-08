"""
Tests for the retry and fall-over behaviour of `StructuredPipeline.run`.

`max_retries` is a total attempt budget spent round-robin over the configured LLM chain. With one LLM that is
the plain retry loop it replaces, which the first tests here hold; with more, an attempt that fails moves to the
next provider rather than spending the whole budget on a dead one.

Nothing here reaches an LLM: `_make_pipeline` is the seam, and every test replaces it.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest
from haystack.core.errors import PipelineRuntimeError
from haystack.dataclasses import ChatMessage
from pydantic import BaseModel

from meri.pipelines.common import StructuredPipeline
from meri.settings.settings import Settings

PRIMARY = {"name": "Primary", "provider": "openai", "model": "gpt-4o-mini", "api_key": "k1"}
BACKUP = {"name": "Backup", "provider": "openai", "model": "gpt-4o", "api_key": "k2"}


class DummyOutput(BaseModel):
    title: str


class DummyPipeline(StructuredPipeline):
    output_model = DummyOutput
    PIPELINE_NAME = "test_retry"


def reply(title: str) -> ChatMessage:
    return ChatMessage.from_assistant(f'{{"title": "{title}"}}', meta={"model": "test-model"})


def stub(dummy: StructuredPipeline, *, per_llm=None, shared=None):
    """
    Replace the `_make_pipeline` seam with mocks, and record which LLM each build was for.

    :param per_llm: Mapping of LLM name to the mock pipeline to hand out for it.
    :param shared: One mock pipeline used for every LLM.
    :return: The list that records build order, by LLM name.
    """
    built: list[str] = []

    def make(llm):
        built.append(llm.name)
        dummy._prompts[llm.name] = MagicMock(variables=["var1"])
        return (per_llm or {}).get(llm.name) or shared or MagicMock()

    dummy._make_pipeline = make  # type: ignore[method-assign]
    return built


def one_llm() -> Settings:
    return Settings(llm=[PRIMARY])


def test_pipeline_retry_success_on_first_try():
    """Verify pipeline returns result immediately when run succeeds."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = {"llm": {"replies": [reply("Hello World")]}}
    stub(dummy, shared=mock_pipeline)

    with patch("meri.pipelines.common.settings", one_llm()):
        res = dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert isinstance(res, DummyOutput)
    assert res.title == "Hello World"
    assert mock_pipeline.run.call_count == 1


def test_pipeline_retry_recovers_after_failure():
    """Verify pipeline retries upon failure and succeeds on subsequent attempt."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = [
        PipelineRuntimeError("llm", object, "LLM validation failed"),
        {"llm": {"replies": [reply("Recovered Title")]}},
    ]
    stub(dummy, shared=mock_pipeline)

    with patch("meri.pipelines.common.settings", one_llm()), patch("time.sleep") as mock_sleep:
        res = dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert isinstance(res, DummyOutput)
    assert res.title == "Recovered Title"
    assert mock_pipeline.run.call_count == 2
    assert mock_sleep.call_count == 1


def test_pipeline_retry_exhausted_raises():
    """Verify pipeline raises exception after all retry attempts are exhausted."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = PipelineRuntimeError("llm", object, "Persistent LLM error")
    stub(dummy, shared=mock_pipeline)

    with (
        patch("meri.pipelines.common.settings", one_llm()),
        patch("time.sleep") as mock_sleep,
        pytest.raises(PipelineRuntimeError, match="Persistent LLM error"),
    ):
        dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert mock_pipeline.run.call_count == 3
    assert mock_sleep.call_count == 2


def test_a_single_llm_chain_behaves_exactly_as_before():
    """
    The regression guard for the fallback default.

    A pipeline with no `pipelines:` entry gets every configured LLM. With one configured that is a chain of one,
    and `max_retries=3` has to mean three calls and two sleeps, as it did before there were chains at all.
    """
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = RuntimeError("down")
    built = stub(dummy, shared=mock_pipeline)

    with (
        patch("meri.pipelines.common.settings", one_llm()),
        patch("time.sleep") as mock_sleep,
        pytest.raises(RuntimeError),
    ):
        dummy.run({"var1": "val1"})

    assert mock_pipeline.run.call_count == 3
    assert mock_sleep.call_count == 2
    assert built == ["Primary"], "the one pipeline must be built once and cached"


def test_attempts_go_round_robin_over_the_chain():
    """
    Assert the order of LLMs, not merely the number of attempts.

    A dead provider must cost one attempt, not the whole budget, so attempt 2 has to land on the backup.
    """
    dummy = DummyPipeline()
    calls: list[str] = []

    def make(llm):
        dummy._prompts[llm.name] = MagicMock(variables=["var1"])
        pipeline = MagicMock()

        def run(_inputs, _name=llm.name):
            calls.append(_name)
            raise RuntimeError(f"{_name} is down")

        pipeline.run.side_effect = run
        return pipeline

    dummy._make_pipeline = make  # type: ignore[method-assign]
    settings = Settings(llm=[PRIMARY, BACKUP])

    with patch("meri.pipelines.common.settings", settings), patch("time.sleep"), pytest.raises(RuntimeError):
        dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert calls == ["Primary", "Backup", "Primary"]


def test_success_on_the_backup_stops_there():
    """Falling over is not a reason to keep going: a good answer from the backup ends the run."""
    dummy = DummyPipeline()
    failing = MagicMock()
    failing.run.side_effect = RuntimeError("Primary is down")
    working = MagicMock()
    working.run.return_value = {"llm": {"replies": [reply("From the backup")]}}
    stub(dummy, per_llm={"Primary": failing, "Backup": working})

    with patch("meri.pipelines.common.settings", Settings(llm=[PRIMARY, BACKUP])), patch("time.sleep"):
        res = dummy.run({"var1": "val1"}, max_retries=3, initial_delay=0.01)

    assert res.title == "From the backup"  # type: ignore[attr-defined]
    assert failing.run.call_count == 1
    assert working.run.call_count == 1


def test_retry_knobs_come_from_the_pipeline_definition():
    """An operator sets the budget in `pipelines:`, so an uninstructed caller must read it from there."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = RuntimeError("down")
    stub(dummy, shared=mock_pipeline)
    settings = Settings(llm=[PRIMARY], pipelines={"test_retry": {"max_retries": 2, "initial_delay": 7.0}})

    with (
        patch("meri.pipelines.common.settings", settings),
        patch("time.sleep") as mock_sleep,
        pytest.raises(RuntimeError),
    ):
        dummy.run({"var1": "val1"})

    assert mock_pipeline.run.call_count == 2
    mock_sleep.assert_called_once_with(7.0)


def test_an_explicit_argument_still_overrides_the_definition():
    """The knobs stay arguments, so a caller that knows better than the configuration can say so."""
    dummy = DummyPipeline()
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = RuntimeError("down")
    stub(dummy, shared=mock_pipeline)
    settings = Settings(llm=[PRIMARY], pipelines={"test_retry": {"max_retries": 9}})

    with patch("meri.pipelines.common.settings", settings), patch("time.sleep"), pytest.raises(RuntimeError):
        dummy.run({"var1": "val1"}, max_retries=2, initial_delay=0.01)

    assert mock_pipeline.run.call_count == 2


def test_each_llm_is_built_once_under_concurrent_runs():
    """
    The cache is populated under a lock, so worker threads cannot each build their own generator.

    One instance is shared across MAX_WORKERS threads once the title pipeline is hoisted out of the per-article
    closure, and a generator carries an HTTP connection pool worth keeping.
    """
    dummy = DummyPipeline()
    threads = 8
    barrier = threading.Barrier(threads)
    built: list[str] = []
    build_lock = threading.Lock()

    def make(llm):
        with build_lock:
            built.append(llm.name)
        dummy._prompts[llm.name] = MagicMock(variables=["var1"])
        pipeline = MagicMock()
        pipeline.run.return_value = {"llm": {"replies": [reply("Concurrent")]}}
        return pipeline

    dummy._make_pipeline = make  # type: ignore[method-assign]
    results: list[str] = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        out = dummy.run({"var1": "val1"}, max_retries=1)
        with results_lock:
            results.append(out.title)  # type: ignore[attr-defined]

    with patch("meri.pipelines.common.settings", one_llm()):
        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join()

    assert built == ["Primary"], f"expected one build, got {built}"
    assert results == ["Concurrent"] * threads
