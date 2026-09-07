"""
Global pytest fixtures for meri test suite.
"""

import pytest

from meri.bootstrap import setup


@pytest.fixture(autouse=True)
def app_context(request, monkeypatch, tmp_path):
    """
    Automatically activate meri application setup context for all tests except bootstrap lifecycle tests.
    """
    # Use the rules built into the Suola module, so signatures are deterministic and no test hits the network.
    monkeypatch.setenv("SUOLA_RULES", "")

    # The container points the XDG roots at `/app/instance`, which is a bind mount of the working copy. Keep a test
    # that writes out of it.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    if request.module and "test_bootstrap" in request.module.__name__:
        yield
        return

    with setup():
        yield
