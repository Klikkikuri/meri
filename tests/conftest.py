"""
Global pytest fixtures for meri test suite.
"""

import pytest

from meri.bootstrap import setup


@pytest.fixture(autouse=True)
def app_context(request, monkeypatch):
    """
    Automatically activate meri application setup context for all tests except bootstrap lifecycle tests.
    """
    # Use the rules built into the Suola module, so signatures are deterministic and no test hits the network.
    monkeypatch.setenv("SUOLA_RULES", "")

    if request.module and "test_bootstrap" in request.module.__name__:
        yield
        return

    with setup():
        yield
