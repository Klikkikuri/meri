"""
Tests for loading Suola rules from the provider manifest.
"""

import json
import os

import pytest
from pydantic import ValidationError
from suola import Suola

from meri import suola as meri_suola
from meri.rahti.storage import HttpsDriver, StoredBlob
from meri.settings.settings import Settings

RULES = {
    "sites": [
        {
            "domain": "example.fi",
            "templates": [{"pattern": "^/a/(?P<Id>\\d+)", "template": "https://canonical.example/{{ .Id }}"}],
        }
    ]
}
MORE_RULES = {"sites": [{"domain": "more.fi"}]}
REMOTE = "https://meri.example/rules.json"


@pytest.fixture(autouse=True)
def reset_suola():
    """`init_suola` stores its instance in the context; keep a test's rules out of every later test."""
    yield
    meri_suola._suola_var.set(None)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Redirect the rules cache into a temporary directory, and return the cache file of `REMOTE`."""
    monkeypatch.setattr(meri_suola, "_cache_dir", lambda: tmp_path / "cache")
    return meri_suola._rules_cache(REMOTE)


def serve(monkeypatch, *answers):
    """Answer remote reads with `answers` in turn, raising the ones that are exceptions. Returns the locations read."""
    calls = []
    pending = iter(answers)

    def read(self, location):
        calls.append(location)
        answer = next(pending)
        if isinstance(answer, Exception):
            raise answer
        return StoredBlob(answer, None)

    monkeypatch.setattr(HttpsDriver, "read", read)
    return calls


def make_stale(path):
    stamp = path.stat().st_mtime - meri_suola.RULES_MAX_AGE.total_seconds() - 1
    os.utime(path, (stamp, stamp))


def write(path, body):
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def write_manifest(directory, *resources):
    return str(
        write(directory / "manifest.json", {"schema_version": "0.1.0", "name": "T", "resources": list(resources)})
    )


def test_a_remote_document_is_cached(cache, monkeypatch):
    serve(monkeypatch, json.dumps(RULES).encode())

    assert json.loads(meri_suola._cached_read(REMOTE).content) == RULES
    assert json.loads(cache.read_text(encoding="utf-8")) == RULES


def test_a_fresh_cache_is_not_refetched(cache, monkeypatch):
    cache.parent.mkdir(parents=True)
    write(cache, RULES)
    calls = serve(monkeypatch)

    assert json.loads(meri_suola._cached_read(REMOTE).content) == RULES
    assert calls == []


def test_a_stale_cache_is_refetched(cache, monkeypatch):
    cache.parent.mkdir(parents=True)
    write(cache, MORE_RULES)
    make_stale(cache)
    serve(monkeypatch, json.dumps(RULES).encode())

    meri_suola._cached_read(REMOTE)

    assert json.loads(cache.read_text(encoding="utf-8")) == RULES


def test_each_location_has_its_own_cache(cache):
    assert meri_suola._rules_cache("https://other.example/rules.json") != cache


@pytest.mark.parametrize("content", [b"<html>404</html>", json.dumps({"nope": []}).encode()])
def test_an_invalid_payload_is_not_cached(cache, monkeypatch, content):
    serve(monkeypatch, content)

    with pytest.raises(ValueError):
        meri_suola._cached_read(REMOTE)
    assert not cache.exists()


def test_a_failed_refresh_uses_the_stale_copy(cache, monkeypatch):
    cache.parent.mkdir(parents=True)
    write(cache, RULES)
    make_stale(cache)
    serve(monkeypatch, OSError("network down"))

    assert json.loads(meri_suola._cached_read(REMOTE).content) == RULES


def test_a_failed_fetch_without_a_copy_raises(cache, monkeypatch):
    serve(monkeypatch, OSError("network down"))

    with pytest.raises(OSError):
        meri_suola._cached_read(REMOTE)


def test_a_local_rules_file_goes_to_suola_as_it_is(tmp_path, cache):
    rules = write(tmp_path / "rules.json", RULES)
    manifest = write_manifest(tmp_path, {"type": "suola-rules", "href": "./rules.json"})

    [item] = meri_suola.load_rules(manifest)

    assert meri_suola._rules_file(item) == rules
    assert not cache.parent.exists()


class FakeSuola:
    """Records how it is built, and refuses the base rules file named `refused.json`."""

    def __init__(self, custom_rules=None):
        if custom_rules is not None and custom_rules.name == "refused.json":
            raise RuntimeError("Exited with i32 exit status 1")
        self.custom_rules = custom_rules
        self.appended = []

    def append_rules(self, rules):
        self.appended.append(json.loads(rules))


def test_the_first_document_is_the_base_and_the_others_append(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(meri_suola, "Suola", FakeSuola)
    write(tmp_path / "rules.json", RULES)
    write(tmp_path / "more.json", MORE_RULES)
    manifest = write_manifest(
        tmp_path,
        {"type": "suola-rules", "href": "./more.json", "weight": 1},
        {"type": "rahti-data", "href": "./absent-data.json"},
        {"type": "suola-rules", "href": "./rules.json"},
    )

    inst = meri_suola.init_suola(manifest)

    assert isinstance(inst, FakeSuola)
    assert inst.custom_rules == tmp_path / "rules.json"
    assert inst.appended == [MORE_RULES]


def test_refused_or_missing_rules_are_skipped(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(meri_suola, "Suola", FakeSuola)
    write(tmp_path / "refused.json", RULES)
    write(tmp_path / "rules.json", RULES)
    manifest = write_manifest(
        tmp_path,
        {"type": "suola-rules", "href": "./absent.json"},
        {"type": "suola-rules", "href": "./refused.json"},
        {"type": "suola-rules", "href": "./rules.json"},
    )

    inst = meri_suola.init_suola(manifest)

    assert isinstance(inst, FakeSuola)
    assert (inst.custom_rules, inst.appended) == (tmp_path / "rules.json", [])


def test_an_href_list_merges_the_documents_that_load_into_one_base(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(meri_suola, "Suola", FakeSuola)
    write(tmp_path / "rules.json", RULES)
    write(tmp_path / "more.json", MORE_RULES)
    manifest = write_manifest(
        tmp_path, {"type": "suola-rules", "href": ["./rules.json", "./absent.json", "./more.json"]}
    )

    inst = meri_suola.init_suola(manifest)

    assert isinstance(inst, FakeSuola) and inst.custom_rules is not None
    assert inst.custom_rules.parent == cache.parent
    assert json.loads(inst.custom_rules.read_text(encoding="utf-8"))["sites"] == RULES["sites"] + MORE_RULES["sites"]


@pytest.mark.parametrize("manifest", [None, "", "absent/manifest.json"])
def test_without_a_manifest_the_built_in_rules_are_used(monkeypatch, manifest):
    monkeypatch.setattr(meri_suola, "Suola", FakeSuola)

    inst = meri_suola.init_suola(manifest)

    assert isinstance(inst, FakeSuola)
    assert inst.custom_rules is None


def test_inline_rules_are_written_for_the_wasm_runtime(tmp_path, cache):
    manifest = write_manifest(tmp_path, {"type": "suola-rules", "rules": RULES})

    [item] = meri_suola.load_rules(manifest)
    path = meri_suola._rules_file(item)

    assert path.parent == cache.parent
    assert json.loads(path.read_text(encoding="utf-8")) == RULES


def test_manifest_rules_decide_the_signature(tmp_path, cache):
    write(tmp_path / "rules.json", RULES)
    manifest = write_manifest(tmp_path, {"type": "suola-rules", "href": "./rules.json"})

    inst = meri_suola.init_suola(manifest)

    assert inst("https://example.fi/a/42?utm_source=x") == Suola()("https://canonical.example/42")


def test_the_removed_suola_rules_setting_is_refused(monkeypatch):
    with pytest.raises(ValidationError, match="suola_rules"):
        Settings.model_validate({"suola_rules": "packages/suola/build/rules.json"})

    monkeypatch.setenv("SUOLA_RULES", "packages/suola/build/rules.json")
    with pytest.raises(ValidationError, match="suola_rules"):
        Settings()
