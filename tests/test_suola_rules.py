"""
Tests for resolving Suola rule locations into a local compiled JSON file.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from meri.suola import RULES_MAX_AGE, resolve_rules

RULES = {"sites": [{"domain": "example.com", "templates": []}]}


@pytest.fixture
def rules_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(RULES), encoding="utf-8")
    return path


@pytest.fixture
def cache(tmp_path):
    """Redirect the download cache into a temporary directory."""
    path = tmp_path / "cache" / "rules.json"
    with patch("meri.suola._rules_cache", return_value=path):
        yield path


def mock_response(content: bytes) -> MagicMock:
    return MagicMock(content=content, raise_for_status=MagicMock())


@pytest.mark.parametrize("location", [None, "", "   "])
def test_empty_location_uses_builtin_rules(location):
    assert resolve_rules(location) is None


def test_plain_path(rules_file):
    assert resolve_rules(str(rules_file)) == rules_file


def test_file_url(rules_file):
    assert resolve_rules(f"file://{rules_file}") == rules_file


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_rules(str(tmp_path / "absent.json"))


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_yaml_location_rejected(tmp_path, suffix):
    path = tmp_path / f"rules{suffix}"
    path.touch()

    with pytest.raises(ValueError, match="compiled JSON"):
        resolve_rules(str(path))


def test_download_writes_cache(cache):
    with patch("meri.suola.requests.get", return_value=mock_response(json.dumps(RULES).encode())) as get:
        assert resolve_rules("https://example.com/rules.json") == cache

    get.assert_called_once()
    assert json.loads(cache.read_text(encoding="utf-8")) == RULES


def test_fresh_cache_is_not_refetched(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(RULES), encoding="utf-8")

    with patch("meri.suola.requests.get") as get:
        assert resolve_rules("https://example.com/rules.json") == cache

    get.assert_not_called()


def test_stale_cache_is_refetched(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text("{}", encoding="utf-8")
    stale = cache.stat().st_mtime - RULES_MAX_AGE.total_seconds() - 1
    os.utime(cache, (stale, stale))

    with patch("meri.suola.requests.get", return_value=mock_response(json.dumps(RULES).encode())):
        assert resolve_rules("https://example.com/rules.json") == cache

    assert json.loads(cache.read_text(encoding="utf-8")) == RULES


@pytest.mark.parametrize("content", [b"<html>404</html>", json.dumps({"nope": []}).encode()])
def test_invalid_payload_is_not_cached(cache, content):
    with patch("meri.suola.requests.get", return_value=mock_response(content)):
        assert resolve_rules("https://example.com/rules.json") is None

    assert not cache.exists()


def test_download_failure_falls_back_to_stale_cache(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(RULES), encoding="utf-8")
    stale = cache.stat().st_mtime - RULES_MAX_AGE.total_seconds() - 1
    os.utime(cache, (stale, stale))

    with patch("meri.suola.requests.get", side_effect=OSError("network down")):
        assert resolve_rules("https://example.com/rules.json") == cache


def test_download_failure_without_cache_uses_builtin_rules(cache):
    with patch("meri.suola.requests.get", side_effect=OSError("network down")):
        assert resolve_rules("https://example.com/rules.json") is None
