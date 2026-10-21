"""Tests for loading manifest resources, with a fake reader in place of the drivers."""

import json

import pytest

from meri.rahti.manifest import loader
from meri.rahti.manifest.loader import load_resources, read_manifest, resolve_href
from meri.rahti.manifest.models import Manifest, RulesDocument, SitesDocument
from meri.rahti.storage import BlobNotFoundError, FileDriver, HttpsDriver, StoredBlob

BASE = "https://meri.example/provider/manifest.json"
RULES = {"sites": [{"domain": "fetched.fi"}]}
SITES = {"schema_version": "0.1.0", "sites": {"www.hs.fi": {"name": "HS"}}}


def manifest(*resources: dict) -> Manifest:
    return Manifest.model_validate({"schema_version": "0.1.0", "name": "Test", "resources": list(resources)})


def reader(documents: dict[str, object]):
    """A reader over `documents`, keyed by location, that records every location it is asked for."""
    calls: list[str] = []

    def read(location: str) -> StoredBlob:
        calls.append(location)
        if location not in documents:
            raise BlobNotFoundError(location)
        body = documents[location]
        return StoredBlob(body if isinstance(body, bytes) else json.dumps(body).encode(), "v1")

    return read, calls


def url(name: str) -> str:
    return f"https://meri.example/provider/{name}"


def test_read_manifest_reads_one_document(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "0.1.0", "name": "T", "resources": [{"type": "rahti-data", "href": "./d.json"}]})
    )
    calls = []
    real_read = FileDriver.read
    monkeypatch.setattr(FileDriver, "read", lambda self, location: calls.append(location) or real_read(self, location))

    read_manifest(str(path))

    assert calls == [str(path)]


def test_only_the_requested_types_are_read():
    read, calls = reader({url("rules.json"): RULES})
    parsed = manifest(
        {"type": "rahti-data", "href": "./data.json"},
        {"type": "suola-rules", "href": "./rules.json"},
        {"type": "site-configs", "href": "./sites.json"},
    )

    loaded = load_resources(parsed, BASE, types={"suola-rules"}, read=read)

    assert calls == [url("rules.json")]
    assert [item.resource.type for item in loaded] == ["suola-rules"]


def test_an_href_list_reads_every_document_and_merges_them_in_order():
    read, calls = reader({url("a.json"): RULES, url("b.json"): {"sites": [{"domain": "other.fi"}]}})
    parsed = manifest({"type": "suola-rules", "href": ["./a.json", "./b.json"]})

    [item] = load_resources(parsed, BASE, read=read)

    assert calls == [url("a.json"), url("b.json")]
    assert (list(item.blobs), item.errors) == ([url("a.json"), url("b.json")], {})
    assert isinstance(item.content, RulesDocument)
    assert [site["domain"] for site in item.content.sites] == ["fetched.fi", "other.fi"]


def test_a_failing_href_is_reported_and_the_rest_of_the_list_merges():
    read, _ = reader({url("b.json"): RULES})
    parsed = manifest({"type": "suola-rules", "href": ["./a.json", "./b.json"]})

    [item] = load_resources(parsed, BASE, read=read)

    assert list(item.errors) == [url("a.json")]
    assert isinstance(item.errors[url("a.json")], BlobNotFoundError)
    assert isinstance(item.content, RulesDocument) and item.content.sites == RULES["sites"]


def test_fetched_content_comes_first_and_inline_last():
    read, _ = reader({url("rules.json"): RULES})
    parsed = manifest({"type": "suola-rules", "href": "./rules.json", "rules": {"sites": [{"domain": "inline.fi"}]}})

    [item] = load_resources(parsed, BASE, read=read)

    assert isinstance(item.content, RulesDocument)
    assert [site["domain"] for site in item.content.sites] == ["fetched.fi", "inline.fi"]


def test_inline_and_linked_forms_load_to_the_same_content():
    read, calls = reader({url("sites.json"): SITES})

    [linked] = load_resources(manifest({"type": "site-configs", "href": "./sites.json"}), BASE, read=read)
    [inline] = load_resources(manifest({"type": "site-configs", "sites": SITES["sites"]}), BASE, read=read)

    assert calls == [url("sites.json")]
    assert isinstance(linked.content, SitesDocument)
    assert linked.content.sites == inline.content.sites  # type: ignore[union-attr]
    assert (inline.blobs, inline.errors) == ({}, {})


def test_a_failing_resource_is_reported_and_the_others_load():
    read, _ = reader({url("rules.json"): RULES, url("bad.json"): b"<html>not json</html>"})
    parsed = manifest(
        {"type": "site-configs", "href": "./absent.json"},
        {"type": "rahti-data", "href": "./bad.json"},
        {"type": "suola-rules", "href": "./rules.json"},
    )

    loaded = {item.resource.type: item for item in load_resources(parsed, BASE, read=read)}

    assert isinstance(loaded["site-configs"].errors[url("absent.json")], BlobNotFoundError)
    assert isinstance(loaded["rahti-data"].errors[url("bad.json")], ValueError)
    assert (loaded["site-configs"].content, loaded["rahti-data"].content) == (None, None)
    assert loaded["suola-rules"].errors == {}


def test_one_driver_serves_every_read_of_a_resolution(monkeypatch):
    drivers = []

    def read(self, location):
        drivers.append(self)
        return StoredBlob(json.dumps(RULES).encode(), None)

    monkeypatch.setattr(HttpsDriver, "read", read)
    parsed = manifest(
        {"type": "suola-rules", "href": ["./a.json", "./b.json"]}, {"type": "suola-rules", "href": "./c.json"}
    )

    assert not any(item.errors for item in load_resources(parsed, BASE))
    assert len(drivers) == 3 and all(driver is drivers[0] for driver in drivers)


def test_unknown_types_are_never_read():
    read, calls = reader({})

    assert load_resources(manifest({"type": "x-private", "href": "./secret.json"}), BASE, read=read) == []
    assert calls == []


def test_one_location_is_read_once():
    read, calls = reader({url("rules.json"): RULES})
    parsed = manifest({"type": "suola-rules", "href": "./rules.json"}, {"type": "suola-rules", "href": "./rules.json"})

    assert not any(item.errors for item in load_resources(parsed, BASE, read=read))
    assert calls == [url("rules.json")]


def test_a_failed_location_is_read_once_and_does_not_spend_the_budget(monkeypatch):
    monkeypatch.setattr(loader, "MAX_FETCHES", 2)
    read, calls = reader({url("rules.json"): RULES})
    parsed = manifest(
        *({"type": "suola-rules", "href": "./absent.json"} for _ in range(3)),
        {"type": "suola-rules", "href": "./rules.json"},
    )

    loaded = load_resources(parsed, BASE, read=read)

    assert calls == [url("absent.json"), url("rules.json")]
    assert all(isinstance(item.errors[url("absent.json")], BlobNotFoundError) for item in loaded[:3])
    assert loaded[-1].errors == {}


@pytest.mark.parametrize(
    "base, href, expected",
    [
        ("packages/rahti/manifest.json", "./data.json", "packages/rahti/data.json"),
        (
            "https://api.github.com/repos/Klikkikuri/rahti/contents/manifest.json?ref=dev",
            "./data.json",
            "https://api.github.com/repos/Klikkikuri/rahti/contents/data.json?ref=dev",
        ),
        (
            "https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/manifest.json",
            "./data.json",
            "https://raw.githubusercontent.com/Klikkikuri/rahti/refs/heads/main/data.json",
        ),
        (BASE, "./data.json", url("data.json")),
    ],
)
def test_resolve_href_follows_the_resolution_table(base, href, expected):
    assert resolve_href(base, href) == expected


def test_the_fetch_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(loader, "MAX_FETCHES", 3)
    read, calls = reader({})
    parsed = manifest(*({"type": "rahti-data", "href": [f"./{i}a.json", f"./{i}b.json"]} for i in range(4)))

    loaded = load_resources(parsed, BASE, read=read)

    assert len(calls) == 3
    assert "fetches" in str(loaded[-1].errors)


def test_the_document_size_is_capped(monkeypatch):
    monkeypatch.setattr(loader, "MAX_DOCUMENT_SIZE", 10)
    read, _ = reader({url("rules.json"): RULES})

    [item] = load_resources(manifest({"type": "suola-rules", "href": "./rules.json"}), BASE, read=read)

    assert "larger than" in str(item.errors)


def test_a_remote_manifest_cannot_link_a_local_file():
    read, calls = reader({})

    [item] = load_resources(manifest({"type": "suola-rules", "href": "file:///etc/rules.json"}), BASE, read=read)

    assert calls == []
    assert "local file" in str(item.errors)
