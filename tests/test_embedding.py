"""
Tests for the process-wide embedding model: what counts as a model, and how a run provisions and loads one.

A configured model is a hard requirement, so what matters here is what counts as "there is a model in this
directory". Get it wrong one way and a run re-fetches a model it already has; get it wrong the other way and an
interrupted download reads as finished for good.
"""

from pathlib import Path

import pytest

from meri.embedding import (
    ensure_model,
    get_embedder,
    load_embedder,
    model_exists,
    provision_model,
)
from meri.settings.embedding import DEFAULT_EMBEDDING_MODEL, model_dir
from meri.settings.settings import Settings

RAHTI = {"url": "file:///app/instance/rahti/data.json"}


def settings_with(**config) -> Settings:
    return Settings.model_validate({"rahti": RAHTI, **config})


def model2vec_layout(path: Path) -> Path:
    """A directory as `save_pretrained` leaves it."""
    path.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "model.safetensors", "tokenizer.json"):
        (path / name).write_text("", encoding="utf-8")
    return path


def sentence_transformers_layout(path: Path) -> Path:
    """The nested layout model2vec also loads, for a model provisioned by other means."""
    (path / "0_StaticEmbedding").mkdir(parents=True)
    (path / "config_sentence_transformers.json").write_text("", encoding="utf-8")
    for name in ("model.safetensors", "tokenizer.json"):
        (path / "0_StaticEmbedding" / name).write_text("", encoding="utf-8")
    return path


def test_an_absent_directory_holds_no_model(tmp_path: Path):
    assert not model_exists(tmp_path / "never-provisioned")


def test_an_empty_directory_holds_no_model(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert not model_exists(empty)


def test_a_half_written_download_is_not_a_model(tmp_path: Path):
    """
    The case that decides the check: `save_pretrained` writes the embeddings BEFORE the tokenizer and config.

    An interrupted fetch leaves exactly this, and calling it a model would make the wreck permanent — nothing
    would ever fetch over it.
    """
    torn = tmp_path / "torn"
    torn.mkdir()
    (torn / "model.safetensors").write_text("", encoding="utf-8")

    assert not model_exists(torn)


def test_a_saved_model_is_found(tmp_path: Path):
    assert model_exists(model2vec_layout(tmp_path / "model"))


def test_the_sentence_transformers_layout_is_found_too(tmp_path: Path):
    assert model_exists(sentence_transformers_layout(tmp_path / "st-model"))


def test_load_embedder_names_the_directory_rather_than_the_hub(tmp_path: Path):
    """
    A configured model that cannot load must fail loudly, never degrade to the built-in mode.

    model2vec reads a path it cannot find as a hub identifier, so without this the operator gets huggingface_hub
    rejecting `/app/instance/embedding/...` as a repo id — an error about a request nobody made. Reaching the
    hub at all would raise that instead of this.
    """
    absent = tmp_path / "not-a-model"

    with pytest.raises(FileNotFoundError, match=str(absent)):
        load_embedder(absent)


def test_load_embedder_refuses_a_directory_that_exists_but_is_empty(tmp_path: Path):
    empty = tmp_path / "empty-model"
    empty.mkdir()

    with pytest.raises(FileNotFoundError):
        load_embedder(empty)


# --- Provisioning ---------------------------------------------------------------


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str]]:
    """Records what would have been fetched, so no test touches the hub."""
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr("meri.embedding.download_model", lambda target, model: calls.append((target, model)))
    return calls


@pytest.fixture
def staged(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """A stubbed fetch that writes a model where it is told, so what `ensure_model` publishes can be read."""
    targets: list[Path] = []

    def fetch(target: Path, _model: str) -> None:
        targets.append(target)
        model2vec_layout(target)

    monkeypatch.setattr("meri.embedding.download_model", fetch)
    return targets


def test_ensure_model_leaves_a_provisioned_directory_alone(tmp_path: Path, downloads: list):
    """The common case: every run after the first must not re-fetch, and must not touch the hub to find out."""
    assert ensure_model(model2vec_layout(tmp_path / "model")) is False
    assert downloads == []


def test_ensure_model_fetches_the_hub_model_the_directory_resolves_from(staged: list):
    """A cold deployment provisions itself from the configuration alone: the directory names the model."""
    destination = model_dir("minishlab/potion-base-8M")

    assert ensure_model(destination) is True
    assert model_exists(destination)


def test_ensure_model_publishes_by_rename(staged: list):
    """
    Runs overlap, so a second process must never read a half-written model.

    The fetch goes to a directory of its own and is renamed into place, exactly as the guard's artifact is.
    """
    destination = model_dir(DEFAULT_EMBEDDING_MODEL)

    ensure_model(destination)

    assert staged and staged[0] != destination
    assert staged[0].parent == destination.parent
    assert not staged[0].exists()


def test_ensure_model_refuses_a_directory_it_cannot_name_a_model_for(tmp_path: Path, downloads: list):
    """A model provisioned by other means: nothing says what to fetch, so say that rather than guess."""
    with pytest.raises(FileNotFoundError, match="download-model"):
        ensure_model(tmp_path / "own-model")

    assert downloads == []


def test_ensure_model_refuses_a_half_written_download(downloads: list):
    """What an interrupted fetch leaves. Overwriting a configured directory unasked is not a run's call."""
    destination = model_dir(DEFAULT_EMBEDDING_MODEL)
    destination.mkdir(parents=True)
    (destination / "model.safetensors").write_text("", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="download-model"):
        ensure_model(destination)

    assert downloads == []


def test_provision_model_fetches_then_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fetching and loading happen here and nowhere else, in that order, and the caller learns of a fetch."""
    events = []
    monkeypatch.setattr("meri.embedding.ensure_model", lambda path: events.append(("fetch", path)) or True)
    monkeypatch.setattr("meri.embedding.load_embedder", lambda path: events.append(("load", path)))
    model = tmp_path / "model"

    assert provision_model(settings_with(embedding={"model": str(model)}), download=True) is True
    assert events == [("fetch", model), ("load", model)]


def test_provision_model_without_download_only_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The opt-out for a deployment that provisions its own model keeps the hub out of the run entirely."""
    monkeypatch.setattr("meri.embedding.ensure_model", lambda _path: pytest.fail("must not reach the hub"))
    monkeypatch.setattr("meri.embedding.load_embedder", lambda _path: None)

    assert provision_model(settings_with(embedding={"model": str(tmp_path)}), download=False) is False


def test_provision_model_does_nothing_without_a_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("meri.embedding.load_embedder", lambda _path: pytest.fail("nothing to load"))

    assert provision_model(settings_with(embedding=None), download=True) is False


def test_get_embedder_is_none_without_a_model():
    assert get_embedder(settings_with(embedding=None)) is None


def test_get_embedder_loads_the_resolved_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def embed(_text):
        return None

    loaded = []
    monkeypatch.setattr("meri.embedding.load_embedder", lambda path: loaded.append(path) or embed)

    assert get_embedder(settings_with(embedding={"model": str(tmp_path)})) is embed
    assert loaded == [tmp_path]
