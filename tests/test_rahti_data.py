"""Tests for pulling and pushing Rahti data through the drivers, and for choosing the Rahti location."""

import json

import pytest

from meri.rahti.data import DEFAULT_LOCATION, Rahti, RahtiData, create_rahti
from meri.rahti.storage import BlobNotFoundError, GitHubDriver
from meri.settings.rahti import RahtiSettings


def test_pull_and_push_through_a_file(tmp_path):
    rahti = Rahti(str(tmp_path / "data.json"))
    data = RahtiData.empty()

    rahti.push(None, data, "msg")
    version, pulled = rahti.pull()

    assert version is None
    assert pulled == data


def test_a_missing_local_file_is_empty_data_and_a_push_creates_it(tmp_path):
    path = tmp_path / "rahti" / "data.json"
    rahti = Rahti(f"file://{path}")

    version, data = rahti.pull()
    assert (version, data.entries) == (None, [])

    rahti.push(version, data, "msg")
    assert path.is_file()


def test_a_github_404_raises_and_nothing_is_pushed(monkeypatch):
    def not_found(self, location):
        raise BlobNotFoundError(location)

    pushed = []
    monkeypatch.setattr(GitHubDriver, "read", not_found)
    monkeypatch.setattr(GitHubDriver, "write", lambda self, *args, **kwargs: pushed.append(args))

    with pytest.raises(BlobNotFoundError):
        Rahti("https://api.github.com/repos/o/r/contents/data.json").pull()
    assert pushed == []


def test_the_location_is_rahti_url():
    assert create_rahti(RahtiSettings(url="file:///elsewhere/data.json")).location == "file:///elsewhere/data.json"


@pytest.mark.parametrize("url", [None, "", "   "])
def test_without_rahti_url_the_default_location_is_used(url):
    assert create_rahti(RahtiSettings(url=url)).location == DEFAULT_LOCATION


def test_the_manifest_does_not_decide_the_location(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema_version": "0.1.0", "name": "T", "resources": [{"type": "rahti-data", "href": "./data.json"}]}
        )
    )
    monkeypatch.setenv("MANIFEST", str(manifest))

    assert create_rahti(RahtiSettings()).location == DEFAULT_LOCATION


def test_a_raw_github_url_is_a_write_target():
    rahti = create_rahti(RahtiSettings(url="https://raw.githubusercontent.com/o/r/refs/heads/main/data.json"))

    assert isinstance(rahti.driver, GitHubDriver)


def test_a_read_only_location_is_refused_and_named():
    with pytest.raises(ValueError, match=r"`rahti.url` https://meri\.example/data\.json is read only"):
        create_rahti(RahtiSettings(url="https://meri.example/data.json"))
