"""
Global pytest fixtures for meri test suite.
"""

import os

import pytest

from meri.bootstrap import setup
from meri.settings.settings import Settings


@pytest.fixture(autouse=True)
def app_context(request, monkeypatch, tmp_path):
    """
    Automatically activate meri application setup context for all tests except bootstrap lifecycle tests.
    """
    # A test states its whole configuration inline; nothing on this machine may leak into it. That takes all
    # four settings sources, not only the YAML files: discovery reads `/app/instance/config.yaml` among other
    # fixed locations, and the environment, `.env` and the secrets directory fill the same fields — a developer
    # with `LUOTSI__SOURCES` in the environment would otherwise fail every test asserting Luotsi is unconfigured.
    monkeypatch.setattr(Settings, "get_default_config_locations", classmethod(lambda cls: []))
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(Settings.model_config, "secrets_dir", None)

    # Niitti also loads the discovered `.env` into `os.environ` from `Settings.__init__`, which puts the file
    # back after this fixture has cleared it. Give it nothing to discover.
    monkeypatch.setattr("niitti.settings.settings.find_dotenv", lambda *args, **kwargs: "")

    fields = {name.lower() for name in Settings.model_fields}
    for variable in list(os.environ):
        # Nested values arrive as `FIELD__SUBFIELD`, so the root names the field either way.
        if variable.split("__", 1)[0].lower() in fields:
            monkeypatch.delenv(variable)

    # Use the rules built into the Suola module, so signatures are deterministic and no test hits the network.
    monkeypatch.setenv("SUOLA_RULES", "")

    # The container points `MERI_DATA_DIR` at `/app/instance`, which is a bind mount of the working copy. Clear it
    # along with the XDG roots, so a test that writes stays out of the working copy.
    monkeypatch.delenv("MERI_DATA_DIR", raising=False)
    monkeypatch.delenv("MERI_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    if request.module and "test_bootstrap" in request.module.__name__:
        yield
        return

    with setup():
        yield
