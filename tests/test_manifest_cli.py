"""Tests for `meri manifest`: the authoring checks, and the generate, validate, show and schema commands."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from meri.cli.manifest import cli, render_schema
from meri.rahti.manifest.checks import check
from meri.rahti.manifest.loader import load_resources
from meri.rahti.manifest.models import Manifest
from meri.rahti.storage import StoredBlob
from meri.settings.settings import Settings

RULES = {"sites": [{"domain": "example.fi"}]}
DATA = {"status": "ok", "schema_version": "0.1.0", "updated": "2026-09-12T09:12:00Z", "entries": []}
SITES = {
    "schema_version": "0.1.0",
    "sites": {"www.hs.fi": {"name": "HS", "rules": [{"container": "aside li", "link": "a", "title": "h3"}]}},
}
CDN_SITES = "https://cdn.example/sites.json"


def write(directory: Path, name: str, body) -> Path:
    path = directory / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@pytest.fixture
def provider(tmp_path) -> Path:
    """A directory holding the three linked documents, and a valid manifest that links them by relative href."""
    write(tmp_path, "rules.json", RULES)
    write(tmp_path, "data.json", DATA)
    write(tmp_path, "sites.json", SITES)
    resources = [
        {"type": "suola-rules", "href": "./rules.json"},
        {"type": "rahti-data", "href": "./data.json"},
        {"type": "site-configs", "href": "./sites.json"},
    ]
    return write(tmp_path, "manifest.json", {"schema_version": "0.1.0", "name": "Test", "resources": resources})


def problems(base: Path, *resources: dict) -> list[str]:
    """Check a manifest of `resources` at `base`. The CDN serves `SITES`; every other href is read from disk."""
    manifest = Manifest.model_validate({"schema_version": "0.1.0", "name": "Test", "resources": list(resources)})

    def read(location: str) -> StoredBlob:
        if location == CDN_SITES:
            return StoredBlob(json.dumps(SITES).encode(), None)
        return StoredBlob(Path(location).read_bytes(), None)

    loaded = load_resources(manifest, str(base), read=read)
    return [str(problem) for problem in check(manifest, str(base), loaded)]


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(cli, list(args), obj={"settings": Settings()}, input=input)


def test_check_suggests_the_type_an_unknown_one_resembles(provider):
    [problem] = problems(provider, {"type": "suola-rule", "href": "./rules.json"})
    assert "did you mean 'suola-rules'?" in problem


def test_check_accepts_private_types(provider):
    assert problems(provider, {"type": "x-private", "href": "./anything.json"}) == []


def test_check_requires_origins_on_a_cross_origin_sites_resource(provider):
    [problem] = problems(provider, {"type": "site-configs", "href": CDN_SITES})
    assert "must declare the `origins`" in problem


@pytest.mark.parametrize(
    "resource",
    [
        {"type": "site-configs", "href": "./sites.json", "origins": ["https://www.hs.fi/*"]},
        {"type": "site-configs", "sites": SITES["sites"], "origins": ["https://www.hs.fi/*"]},
    ],
)
def test_check_refuses_origins_on_a_same_origin_or_inline_sites_resource(provider, resource):
    [problem] = problems(provider, resource)
    assert "only a cross-origin `href` may declare" in problem


def test_check_reports_origins_that_disagree_with_the_document(provider):
    found = problems(provider, {"type": "site-configs", "href": CDN_SITES, "origins": ["https://other.fi/*"]})

    assert len(found) == 2
    assert "does not declare ['https://www.hs.fi/*']" in found[0]
    assert "declares ['https://other.fi/*']" in found[1]


def test_check_accepts_origins_that_match_the_document(provider):
    assert problems(provider, {"type": "site-configs", "href": CDN_SITES, "origins": ["https://www.hs.fi/*"]}) == []


def test_check_reports_linked_documents_that_fail_their_own_format(provider):
    write(provider.parent, "bad-data.json", {**DATA, "entries": [{"title": "no update time"}]})
    write(provider.parent, "bad-rules.json", {"sites": [{"domain": 3}]})

    found = problems(
        provider,
        {"type": "rahti-data", "href": "./bad-data.json"},
        {"type": "suola-rules", "href": "./bad-rules.json"},
        {"type": "site-configs", "href": "./absent.json"},
    )

    assert any("not valid Rahti data" in p for p in found)
    assert any("Suola cannot use the rules" in p for p in found)
    assert any("cannot load" in p for p in found)


def test_check_reports_an_invalid_entry_that_a_later_document_replaces(provider):
    url = {"sign": "s1", "labels": []}
    entry = {"title": "t", "urls": [url], "updated": "2026-09-12T09:12:00Z"}
    write(provider.parent, "bad-data.json", {**DATA, "entries": [{"title": "no update time", "urls": [url]}]})
    write(provider.parent, "good-data.json", {**DATA, "entries": [entry]})

    [problem] = problems(provider, {"type": "rahti-data", "href": ["./bad-data.json", "./good-data.json"]})

    assert "bad-data.json is not valid Rahti data" in problem


def test_validate_passes_a_valid_manifest(provider):
    result = invoke("validate", str(provider))
    assert result.exit_code == 0, result.output
    assert "valid" in result.output


def test_validate_fails_on_a_problem(provider):
    (provider.parent / "data.json").unlink()

    result = invoke("validate", str(provider))

    assert result.exit_code == 1
    assert "cannot load" in result.output


def test_show_merges_each_type_in_precedence_order(provider):
    write(provider.parent, "more-rules.json", {"sites": [{"domain": "more.fi"}]})
    manifest = json.loads(provider.read_text())
    manifest["resources"].insert(0, {"type": "suola-rules", "href": "./more-rules.json", "weight": 1})
    provider.write_text(json.dumps(manifest))

    result = invoke("show", str(provider))

    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert [site["domain"] for site in shown["suola-rules"]["sites"]] == ["example.fi", "more.fi"]
    assert set(shown) == {"suola-rules", "rahti-data", "site-configs"}


def test_schema_prints_the_committed_schema():
    result = invoke("schema")
    assert result.output == render_schema() + "\n"


def test_generate_writes_an_all_relative_manifest_and_keeps_it_when_nothing_changes(provider):
    path = provider.parent / "generated.json"

    first = invoke("generate", "--no-input", str(path))
    written = path.read_text()
    second = invoke("generate", "--no-input", str(path))

    assert (first.exit_code, second.exit_code) == (0, 0), first.output + second.output
    assert "unchanged" in second.output
    assert path.read_text() == written
    manifest = json.loads(written)
    assert [r["href"] for r in manifest["resources"]] == ["./rules.json", "./data.json", "./sites.json"]
    assert "origins" not in written
    assert invoke("validate", str(path)).exit_code == 0


def test_generate_takes_defaults_from_the_existing_manifest(provider):
    manifest = json.loads(provider.read_text())
    manifest["name"] = "Existing"
    manifest["resources"][1] = {"type": "rahti-data", "href": "./other.json", "weight": 3}
    manifest["resources"].append({"type": "x-private", "href": "./x.json"})
    provider.write_text(json.dumps(manifest))

    assert invoke("generate", "--no-input", str(provider)).exit_code == 0

    regenerated = json.loads(provider.read_text())
    assert regenerated["name"] == "Existing"
    assert {"type": "rahti-data", "href": "./other.json", "weight": 3} in regenerated["resources"]
    assert {"type": "x-private", "href": "./x.json"} in regenerated["resources"]


def test_generate_prefers_options_over_prompts(provider, monkeypatch):
    monkeypatch.setattr("meri.cli.manifest._is_terminal", lambda: True)
    path = provider.parent / "generated.json"

    # Options give the name and the `rahti-data` href, so only the `suola-rules` and `site-configs` hrefs are asked for.
    result = invoke(
        "generate", "--name", "Option", "--rahti-data", "./data.json", str(path), input="./prompted.json\n\n"
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads(path.read_text())
    assert manifest["name"] == "Option"
    assert [r["href"] for r in manifest["resources"]] == ["./prompted.json", "./data.json", "./sites.json"]


def test_generate_never_prompts_with_no_input(provider, monkeypatch):
    monkeypatch.setattr("meri.cli.manifest._is_terminal", lambda: True)

    # A prompt would read end of input and abort.
    result = invoke("generate", "--no-input", str(provider.parent / "generated.json"), input="")

    assert result.exit_code == 0, result.output


def test_generate_declares_the_origins_of_a_cross_origin_sites_document(provider, monkeypatch):
    monkeypatch.setattr(
        "meri.rahti.storage.HttpsDriver.read", lambda self, location: StoredBlob(json.dumps(SITES).encode(), None)
    )
    path = provider.parent / "generated.json"

    assert invoke("generate", "--no-input", "--site-configs", CDN_SITES, str(path)).exit_code == 0

    [sites] = [r for r in json.loads(path.read_text())["resources"] if r["type"] == "site-configs"]
    assert sites == {"type": "site-configs", "href": CDN_SITES, "origins": ["https://www.hs.fi/*"]}
