"""
Tests for the embedding model loader.

A configured model is a hard requirement, so what matters here is what counts as "there is a model in this
directory". Get it wrong one way and a run re-fetches a model it already has; get it wrong the other way and an
interrupted download reads as finished for good.
"""

from pathlib import Path

import pytest

from meri.luotsi.embeddings import load_embedder, model_exists


def model2vec_layout(path: Path) -> Path:
    """A directory as `save_pretrained` leaves it."""
    path.mkdir(parents=True)
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
