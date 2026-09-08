"""
Tests for the embedding model Meri hands to Luotsi.

Luotsi takes a directory and carries no default, so naming the model and resolving it is Meri's job. These
guard that resolution: what an unset key, a hub identifier, a path and an explicit null each turn into.
"""

from pathlib import Path

from luotsi.settings import LuotsiSettings

from meri.settings.luotsi import (
    DEFAULT_EMBEDDING_MODEL,
    embedding_dir,
    hub_id_of,
    model_dir,
)
from meri.settings.settings import Settings

RAHTI = {"url": "file:///app/instance/rahti/data.json"}


def luotsi_settings(luotsi: dict) -> LuotsiSettings:
    settings = Settings.model_validate({"rahti": RAHTI, "luotsi": luotsi})
    assert settings.luotsi is not None
    return settings.luotsi


def test_an_unset_model_resolves_to_the_default_one():
    assert luotsi_settings({"sources": []}).embedding_model == model_dir(DEFAULT_EMBEDDING_MODEL)


def test_a_hub_identifier_resolves_under_the_data_directory():
    resolved = luotsi_settings({"embedding_model": "minishlab/potion-base-8M"}).embedding_model

    assert resolved == embedding_dir() / "minishlab" / "potion-base-8M"


def test_a_configured_path_is_left_alone(tmp_path: Path):
    """A model provisioned by other means: Meri must not rewrite it into its own data directory."""
    assert luotsi_settings({"embedding_model": str(tmp_path)}).embedding_model == tmp_path


def test_an_explicit_null_selects_the_builtin_mode():
    assert luotsi_settings({"embedding_model": None}).embedding_model is None


def test_settings_built_in_code_get_the_default_too():
    """The devcontainer and the tests construct Settings directly, not from YAML."""
    settings = Settings.model_validate({"rahti": RAHTI, "luotsi": LuotsiSettings()})

    assert settings.luotsi is not None
    assert settings.luotsi.embedding_model == model_dir(DEFAULT_EMBEDDING_MODEL)


def test_settings_built_in_code_keep_an_explicit_choice(tmp_path: Path):
    settings = Settings.model_validate({"rahti": RAHTI, "luotsi": LuotsiSettings(embedding_model=tmp_path)})

    assert settings.luotsi is not None
    assert settings.luotsi.embedding_model == tmp_path


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
    settings = luotsi_settings({"embedding_model": "minishlab/potion-multilingual-128M"})

    assert settings.embedding_model_id == "minishlab/potion-multilingual-128M"
    assert settings.embedding_model.name == "potion-multilingual-128M"  # what it used to record


def test_two_orgs_sharing_a_model_name_are_told_apart():
    ours = luotsi_settings({"embedding_model": "minishlab/potion-multilingual-128M"})
    theirs = luotsi_settings({"embedding_model": "otherorg/potion-multilingual-128M"})

    assert ours.embedding_model.name == theirs.embedding_model.name
    assert ours.embedding_model_id != theirs.embedding_model_id


def test_a_path_configured_model_is_identified_by_its_directory_name(tmp_path: Path):
    """
    Not the absolute path: that is machine-specific, so moving an identical model would read as a different
    one and retrain for nothing.
    """
    model = tmp_path / "our-own-model"

    assert luotsi_settings({"embedding_model": str(model)}).embedding_model_id == "our-own-model"


def test_settings_built_in_code_get_an_identity_too():
    settings = Settings.model_validate({"rahti": RAHTI, "luotsi": LuotsiSettings()})

    assert settings.luotsi is not None
    assert settings.luotsi.embedding_model_id == DEFAULT_EMBEDDING_MODEL


def test_the_builtin_mode_has_no_model_to_identify():
    assert luotsi_settings({"embedding_model": None}).embedding_model_id is None
