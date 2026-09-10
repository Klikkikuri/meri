"""
Tests for the one embedding model Meri names.

`embedding:` is the only place a model is named; Luotsi and the title guards share it. These guard the
resolution: what an unset key, a hub identifier, a path and an explicit null each turn into, and what the
injection guard's artifact records as the model's identity.
"""

from pathlib import Path

import pytest

from meri.embedding import embedding_model, model_identity
from meri.luotsi.settings import LuotsiSettings, default_vectors
from meri.settings.embedding import (
    DEFAULT_EMBEDDING_MODEL,
    LocalEmbeddingSettings,
    embedding_dir,
    hub_id_of,
    model_dir,
)
from meri.settings.settings import Settings

RAHTI = {"url": "file:///app/instance/rahti/data.json"}


def settings_with(**config) -> Settings:
    return Settings.model_validate({"rahti": RAHTI, **config})


def test_an_unset_key_resolves_to_the_default_model():
    assert embedding_model(settings_with()) == model_dir(DEFAULT_EMBEDDING_MODEL)


def test_a_hub_identifier_resolves_under_the_data_directory():
    resolved = embedding_model(settings_with(embedding={"model": "minishlab/potion-base-8M"}))

    assert resolved == embedding_dir() / "minishlab" / "potion-base-8M"


def test_a_configured_path_is_left_alone(tmp_path: Path):
    """A model provisioned by other means: Meri must not rewrite it into its own data directory."""
    assert embedding_model(settings_with(embedding={"model": str(tmp_path)})) == tmp_path


def test_an_explicit_null_selects_the_builtin_mode():
    settings = settings_with(embedding=None)

    assert settings.embedding is None
    assert embedding_model(settings) is None
    assert model_identity(settings) is None


def test_a_path_not_downloaded_yet_is_accepted(tmp_path: Path):
    """`meri feedback download-model` creates this directory, so on a cold start nothing of it exists."""
    target = tmp_path / "absent" / "model"

    assert LocalEmbeddingSettings(model=str(target)).path == target


def test_a_path_that_is_a_file_is_rejected(tmp_path: Path):
    not_a_directory = tmp_path / "model.bin"
    not_a_directory.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        LocalEmbeddingSettings(model=str(not_a_directory))


def test_an_unknown_provider_is_rejected():
    """The seat for a remote provider exists; sitting in it before it does must fail, not silently go local."""
    with pytest.raises(ValueError, match="provider"):
        settings_with(embedding={"provider": "openai", "model": "text-embedding-3-small"})


def test_the_model_is_no_longer_named_under_luotsi():
    """A stale `luotsi.embedding_model` must fail loudly: silently ignoring it would run a different model."""
    with pytest.raises(ValueError, match="embedding_model"):
        settings_with(luotsi={"embedding_model": "minishlab/potion-base-8M"})


def test_luotsi_resolves_its_artifact_path_by_itself():
    """An explicit null is not a mode for a destination: the guard needs somewhere to write."""
    assert LuotsiSettings().guard_vectors == default_vectors()
    assert LuotsiSettings.model_validate({"guard_vectors": None}).guard_vectors == default_vectors()


def test_hub_id_of_inverts_the_resolution():
    """`download-model` recovers the identifier from the path, so settings need only hold the path."""
    assert hub_id_of(model_dir(DEFAULT_EMBEDDING_MODEL)) == DEFAULT_EMBEDDING_MODEL


def test_hub_id_of_a_path_outside_the_data_directory_is_none(tmp_path: Path):
    assert hub_id_of(tmp_path) is None


# --- The identity the guard's artifact records --------------------------------


def test_a_hub_model_is_identified_by_its_qualified_id():
    """
    The bare directory name cannot tell two same-named models from different orgs apart.

    Neither can the dimension, so a guard would score one model's centroids against the other's embeddings and
    fail open — passing every injection, or silencing readers wholesale.
    """
    settings = settings_with(embedding={"model": "minishlab/potion-multilingual-128M"})

    assert model_identity(settings) == "minishlab/potion-multilingual-128M"
    assert embedding_model(settings).name == "potion-multilingual-128M"  # what it used to record


def test_two_orgs_sharing_a_model_name_are_told_apart():
    ours = settings_with(embedding={"model": "minishlab/potion-multilingual-128M"})
    theirs = settings_with(embedding={"model": "otherorg/potion-multilingual-128M"})

    assert embedding_model(ours).name == embedding_model(theirs).name
    assert model_identity(ours) != model_identity(theirs)


def test_a_path_configured_model_is_identified_by_its_directory_name(tmp_path: Path):
    """
    Not the absolute path: that is machine-specific, so moving an identical model would read as a different
    one and retrain for nothing.
    """
    model = tmp_path / "our-own-model"

    assert model_identity(settings_with(embedding={"model": str(model)})) == "our-own-model"


def test_the_default_model_has_an_identity_too():
    assert model_identity(settings_with()) == DEFAULT_EMBEDDING_MODEL
