"""
The Suola release wheel, the installed package and the `packages/suola` submodule must be the same release.

uv cannot update a URL source, so they drift apart unless `scripts/sync-suola.sh` moves them together.
"""

import importlib.metadata
import re
import shutil
import subprocess  # nosec B404
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUBMODULE = "packages/suola"


@pytest.fixture(scope="module")
def pinned_version() -> str:
    """The version of the release wheel in `pyproject.toml`."""
    sources = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["sources"]["suola"]
    urls = [source["url"] for source in sources if "url" in source]
    assert len(urls) == 1, f"Expected one Suola release wheel in [tool.uv.sources], found {urls}"

    match = re.search(r"/download/v(?P<version>[^/]+)/suola-(?P=version)-py3-none-any\.whl$", urls[0])
    assert match, f"Not a Suola release wheel URL: {urls[0]}"
    return match["version"]


def git(*args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise OSError("git is not installed")
    # The arguments are fixed by the tests, not taken from input.
    result = subprocess.run([executable, *args], cwd=ROOT, capture_output=True, text=True, check=True)  # nosec B603
    return result.stdout.strip()


def test_installed_suola_is_the_pinned_release(pinned_version):
    assert importlib.metadata.version("suola") == pinned_version, "Run `uv sync`."


def test_submodule_points_at_the_pinned_release(pinned_version):
    try:
        pointer = git("ls-tree", "HEAD", SUBMODULE).split()[2]
        tag = git("-C", SUBMODULE, "rev-parse", "--verify", f"refs/tags/v{pinned_version}^{{commit}}")
    except (OSError, subprocess.CalledProcessError, IndexError):
        pytest.skip(f"{SUBMODULE} or its tags are not available")

    assert pointer == tag, f"{SUBMODULE} is not at v{pinned_version}. Run `scripts/sync-suola.sh v{pinned_version}`."
