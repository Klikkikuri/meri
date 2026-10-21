"""Tests for the storage drivers: location parsing, joining, and reads and writes with HTTP mocked."""

import base64
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from pydantic import SecretStr
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse

from meri.rahti import storage
from meri.rahti.storage import (
    DEFAULT_TIMEOUT,
    BlobNotFoundError,
    Driver,
    FileDriver,
    GitHubDriver,
    GitHubLocation,
    HttpsDriver,
    driver_for,
    register,
)


def response(
    status: int = 200,
    body=None,
    content: bytes = b"",
    headers: dict | None = None,
    chunks: list[bytes] | None = None,
) -> MagicMock:
    """A streamed response. Its body is `body` as JSON, else `content`; `chunks` sets how the body arrives."""
    payload = json.dumps(body).encode() if body is not None else content
    res = MagicMock(status_code=status, ok=status < 400, headers=headers or {}, text="")
    res.raw.read1.side_effect = [*(chunks if chunks is not None else [payload]), b""]
    res.json.return_value = body
    res.raise_for_status.side_effect = requests.HTTPError(str(status)) if status >= 400 else None
    return res


def https() -> HttpsDriver:
    driver = HttpsDriver()
    driver.session = MagicMock()
    return driver


def github(token: str | None = None, timeout: int = 12) -> GitHubDriver:
    settings = SimpleNamespace(
        auth_token=SecretStr(token) if token else None,
        timeout=timeout,
        committer=SimpleNamespace(name="bot", email="bot@example.fi"),
    )
    driver = GitHubDriver(settings)  # type: ignore[arg-type]
    driver.session = MagicMock()
    return driver


def envelope(content: bytes, sha: str = "sha1") -> dict:
    return {"type": "file", "sha": sha, "encoding": "base64", "content": base64.b64encode(content).decode()}


@pytest.mark.parametrize(
    "location, expected",
    [
        (
            "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json",
            GitHubLocation("Klikkikuri", "rahti", None, "data.json", None),
        ),
        ("https://api.github.com/repos/o/r/contents/dir/data.json?ref=dev", ("o", "r", "dev", "dir/data.json", None)),
        ("https://api.github.com/repos/o/r/contents/data.json?ref=feat/x", ("o", "r", "feat/x", "data.json", None)),
        (
            "https://raw.githubusercontent.com/o/r/refs/heads/main/data.json",
            ("o", "r", "main", "data.json", "refs/heads/main"),
        ),
        ("https://raw.githubusercontent.com/o/r/refs/tags/v1/a/b.json", ("o", "r", "v1", "a/b.json", "refs/tags/v1")),
        ("https://raw.githubusercontent.com/o/r/main/data.json", ("o", "r", "main", "data.json", "main")),
    ],
)
def test_github_url_forms(location, expected):
    assert GitHubDriver.parse(location) == expected


@pytest.mark.parametrize(
    "base, expected",
    [
        ("packages/rahti/manifest.json", "packages/rahti/data.json"),
        ("file:///app/rahti/manifest.json", "file:///app/rahti/data.json"),
        (
            "https://api.github.com/repos/Klikkikuri/rahti/contents/manifest.json?ref=dev",
            "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json?ref=dev",
        ),
        (
            "https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/manifest.json",
            "https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/data.json",
        ),
        ("https://meri.example/lapinkansa/manifest.json", "https://meri.example/lapinkansa/data.json"),
    ],
)
def test_join_keeps_the_form_and_ref_of_the_base(base, expected):
    assert driver_for(base).join(base, "./data.json") == expected


@pytest.mark.parametrize("base", ["packages/rahti/manifest.json", "https://raw.githubusercontent.com/o/r/main/m.json"])
def test_join_returns_an_absolute_href_unchanged(base):
    assert driver_for(base).join(base, "https://cdn.example/data.json") == "https://cdn.example/data.json"


def test_github_join_refuses_to_leave_the_repository():
    with pytest.raises(ValueError, match="outside the repository"):
        GitHubDriver().join("https://raw.githubusercontent.com/o/r/main/manifest.json", "../other/data.json")


@pytest.mark.parametrize(
    "location, driver",
    [
        ("packages/rahti/data.json", FileDriver),
        ("file:///app/instance/rahti/data.json", FileDriver),
        ("https://api.github.com/repos/o/r/contents/data.json", GitHubDriver),
        ("https://raw.githubusercontent.com/o/r/main/data.json", GitHubDriver),
        ("https://meri.example/data.json", HttpsDriver),
    ],
)
def test_driver_for_picks_by_location(location, driver):
    assert type(driver_for(location)) is driver


@pytest.mark.parametrize("location", ["http://meri.example/data.json", "ftp://meri.example/data.json"])
def test_driver_for_refuses_an_unknown_scheme(location):
    with pytest.raises(ValueError, match="No storage driver"):
        driver_for(location)


def test_registry_tries_drivers_by_descending_weight(monkeypatch):
    monkeypatch.setattr(storage, "_drivers", list(storage._drivers))

    @register(weight=1000)
    class Everything(Driver):
        name = "everything"

        @classmethod
        def matches(cls, location: str) -> bool:
            return True

    assert type(driver_for("packages/rahti/data.json")) is Everything


def test_raw_url_is_read_through_the_contents_api():
    driver = github()
    driver.session.get.return_value = response(body=envelope(b'{"a": 1}', sha="abc"))

    content, version = driver.read("https://raw.githubusercontent.com/o/r/refs/heads/dev/data.json")

    assert (content, version) == (b'{"a": 1}', "abc")
    assert driver.session.get.call_args.args[0] == "https://api.github.com/repos/o/r/contents/data.json?ref=dev"


def test_a_large_file_is_refetched_raw():
    driver = github()
    driver.session.get.side_effect = [
        response(body={"type": "file", "sha": "abc", "encoding": "none", "content": ""}),
        response(content=b"large"),
    ]

    assert driver.read("https://api.github.com/repos/o/r/contents/data.json") == (b"large", "abc")
    assert driver.session.get.call_args.kwargs["headers"]["Accept"] == "application/vnd.github.raw+json"


def test_a_github_404_is_not_found():
    driver = github()
    driver.session.get.return_value = response(404)

    with pytest.raises(BlobNotFoundError):
        driver.read("https://api.github.com/repos/o/r/contents/data.json")


def test_a_github_403_raises_the_http_error():
    driver = github()
    driver.session.get.return_value = response(403)

    with pytest.raises(requests.HTTPError):
        driver.read("https://api.github.com/repos/o/r/contents/data.json")


@pytest.mark.parametrize("token", [None, "ghp_secret"])
def test_authorization_only_with_a_token_and_the_configured_timeout(token):
    driver = github(token=token, timeout=7)
    driver.session.get.return_value = response(body=envelope(b"{}"))

    driver.read("https://api.github.com/repos/o/r/contents/data.json")

    call = driver.session.get.call_args
    assert call.kwargs["timeout"] == 7
    assert call.kwargs["headers"].get("Authorization") == (f"Bearer {token}" if token else None)


def test_a_write_sends_sha_branch_and_committer():
    driver = github(token="t")  # nosec B106
    driver.session.put.return_value = response(body={"content": {"sha": "new"}})

    version = driver.write(
        "https://raw.githubusercontent.com/o/r/refs/heads/dev/data.json", b"{}", version="old", message="msg"
    )

    assert version == "new"
    call = driver.session.put.call_args
    assert call.args[0] == "https://api.github.com/repos/o/r/contents/data.json"
    assert call.kwargs["json"] == {
        "message": "msg",
        "content": base64.b64encode(b"{}").decode(),
        "committer": {"name": "bot", "email": "bot@example.fi"},
        "sha": "old",
        "branch": "dev",
    }


def test_the_https_driver_reads_with_its_etag_and_refuses_to_write():
    driver = https()
    driver.session.get.return_value = response(content=b"{}", headers={"ETag": '"v1"'})

    assert driver.read("https://meri.example/data.json") == (b"{}", '"v1"')
    assert not driver.writable
    with pytest.raises(NotImplementedError):
        driver.write("https://meri.example/data.json", b"{}", version=None, message="msg")


def test_file_driver_writes_atomically_and_reads_back(tmp_path):
    location = f"file://{tmp_path}/nested/data.json"
    driver = driver_for(location)

    driver.write(location, b"first", version=None, message="msg")
    driver.write(location, b"second", version=None, message="msg")

    assert driver.read(location) == (b"second", None)
    assert [p.name for p in (tmp_path / "nested").iterdir()] == ["data.json"]
    assert stat.S_IMODE((tmp_path / "nested" / "data.json").stat().st_mode) == 0o644


def test_file_writes_use_their_own_temporary_files_and_keep_permissions(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    path.write_bytes(b"old")
    path.chmod(0o640)
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(storage.os, "replace", lambda src, dst: replaced.append(src) or real_replace(src, dst))

    FileDriver().write(str(path), b"first", version=None, message="msg")
    FileDriver().write(str(path), b"second", version=None, message="msg")

    assert len(set(replaced)) == 2
    assert all(Path(src).parent == tmp_path for src in replaced)
    assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) == (b"second", 0o640)


def test_file_driver_reports_a_missing_file(tmp_path):
    with pytest.raises(BlobNotFoundError):
        FileDriver().read(str(tmp_path / "absent.json"))


@pytest.mark.parametrize(
    "make, location",
    [(https, "https://attacker.example/rules.json"), (github, "https://api.github.com/repos/o/r/contents/rules.json")],
)
def test_a_redirect_is_refused_and_not_followed(make, location):
    driver = make()
    redirect = response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    driver.session.get.return_value = redirect

    with pytest.raises(OSError, match=r"redirects to 'http://169\.254\.169\.254/latest/meta-data/'"):
        driver.read(location)

    assert driver.session.get.call_args.kwargs["allow_redirects"] is False
    redirect.raw.read1.assert_not_called()


def test_a_github_write_refuses_a_redirect():
    driver = github(token="t")  # nosec B106
    driver.session.put.return_value = response(307, headers={"Location": "http://internal.example/"})

    with pytest.raises(OSError, match="redirects"):
        driver.write("https://api.github.com/repos/o/r/contents/data.json", b"{}", version=None, message="msg")
    assert driver.session.put.call_args.kwargs["allow_redirects"] is False


def test_a_document_over_the_size_limit_is_refused_while_it_downloads(monkeypatch):
    monkeypatch.setattr(storage, "MAX_DOCUMENT_SIZE", 10)
    driver = https()
    streamed = response(chunks=[b"x" * 8] * 3)
    driver.session.get.return_value = streamed

    with pytest.raises(ValueError, match="larger than 10 bytes"):
        driver.read("https://meri.example/big.json")

    assert driver.session.get.call_args.kwargs["stream"] is True
    assert streamed.raw.read1.call_count == 2
    streamed.close.assert_called_once()


def test_a_slow_transfer_stops_at_the_total_timeout(monkeypatch):
    # Start, then after the first and second chunks. The default timeout is 30 seconds.
    clock = iter([0.0, 10.0, 31.0])
    monkeypatch.setattr(storage, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    driver = https()
    streamed = response(chunks=[b"a", b"b", b"c"])
    driver.session.get.return_value = streamed

    with pytest.raises(TimeoutError, match="longer than 30 seconds"):
        driver.read("https://meri.example/slow.json")
    assert streamed.raw.read1.call_count == 2


def test_a_retry_after_header_does_not_hold_a_read_past_the_timeout(monkeypatch):
    sleeps = []
    monkeypatch.setattr("urllib3.util.retry.time.sleep", sleeps.append)
    adapter = HttpsDriver().session.get_adapter("https://meri.example/")
    assert isinstance(adapter, HTTPAdapter)
    retry = adapter.max_retries
    assert isinstance(retry.total, int)
    throttled = HTTPResponse(status=429, headers={"Retry-After": "3600"})

    for _ in range(retry.total):
        retry = retry.increment("GET", "/data.json", response=throttled)
        retry.sleep(throttled)

    assert sum(sleeps) < DEFAULT_TIMEOUT
