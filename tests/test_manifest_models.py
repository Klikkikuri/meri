"""Tests for the manifest models and merge rules. Pure: no drivers, no mocks."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from meri.cli.manifest import render, render_schema
from meri.rahti.manifest.models import (
    Link,
    Manifest,
    RahtiDocument,
    RulesDocument,
    Site,
    SiteRule,
    SitesDocument,
    SitesResource,
    SuolaRulesResource,
    UnknownResource,
    check_compatible,
    merge_rahti,
    merge_sites,
)

MANIFEST = {
    "$schema": "https://raw.githubusercontent.com/Klikkikuri/meri/refs/heads/main/docs/manifest.schema.json",
    "schema_version": "0.1.0",
    "name": "Klikkikuri",
    "updated": "2026-09-12T09:12:00Z",
    "links": [{"rel": "about", "href": "https://github.com/Klikkikuri"}],
    "resources": [
        {"type": "suola-rules", "href": "./rules.json"},
        {"type": "rahti-data", "href": "./data.json"},
        {"type": "site-configs", "href": "./sites.json"},
    ],
}

RULE = SiteRule(container="aside li", link="a", title="h3")


def manifest(*resources: dict, **fields) -> Manifest:
    return Manifest.model_validate({**MANIFEST, "resources": list(resources), **fields})


def test_the_example_manifest_parses():
    parsed = Manifest.model_validate(MANIFEST)
    assert [r.type for r in parsed.ordered()] == ["suola-rules", "rahti-data", "site-configs"]


@pytest.mark.parametrize("kind", ["x-private", "news-sources"])
def test_unknown_and_private_types_parse_and_are_skipped(kind):
    parsed = manifest({"type": kind, "href": "./other.json", "extra": {"kept": True}})

    assert isinstance(parsed.resources[0], UnknownResource)
    assert parsed.ordered() == []


def test_an_unknown_rel_parses():
    assert Link(rel="x-something-new", href="https://example.fi/").rel == "x-something-new"


@pytest.mark.parametrize(
    "resource",
    [
        {"type": "rahti-data", "href": "./data.json", "weight": -1},
        {"type": "rahti-data"},
        {"type": "rahti-data", "href": []},
        {"type": "site-configs", "href": "https://cdn.example/sites.json", "origins": ["https://*.example.fi/*"]},
        {"type": "site-configs", "href": "https://cdn.example/sites.json", "origins": ["*://*/*"]},
        {"type": "site-configs", "href": "https://cdn.example/sites.json", "origins": ["<all_urls>"]},
    ],
)
def test_invalid_resources_are_rejected(resource):
    with pytest.raises(ValidationError):
        manifest(resource)


@pytest.mark.parametrize("href", ["javascript:alert(1)", "mailto:a@example.fi", "/relative"])
def test_a_link_must_be_http(href):
    with pytest.raises(ValidationError):
        Link(rel="about", href=href)


@pytest.mark.parametrize(
    "version, supported, compatible",
    [
        ("0.1.0", "0.1.0", True),
        ("0.1.7", "0.1.0", True),
        ("0.2.0", "0.1.0", False),
        ("1.0.0", "0.1.0", False),
        ("1.4.0", "1.0.0", True),
        ("2.0.0", "1.0.0", False),
    ],
)
def test_check_compatible_follows_the_caret_rule(version, supported, compatible):
    if compatible:
        assert check_compatible(version, supported) == version
    else:
        with pytest.raises(ValueError, match="not supported"):
            check_compatible(version, supported)


@pytest.mark.parametrize("version", ["0.1", "v0.1.0", "01.0.0"])
def test_a_version_must_be_semantic(version):
    with pytest.raises(ValueError, match="not a semantic version"):
        check_compatible(version, "0.1.0")


def test_the_schema_version_is_checked_per_type():
    # The manifest and `sites.json` are in initial development, so a new minor version is not compatible.
    manifest(schema_version="0.1.7")
    for version in ("0.2.0", "1.0.0"):
        with pytest.raises(ValidationError, match=r"0\.1\.x expected"):
            manifest(schema_version=version)
    with pytest.raises(ValidationError, match=r"0\.1\.x expected"):
        SitesDocument.model_validate({"schema_version": "0.2.0", "sites": {}})

    # Rahti data keeps the major-only check that released Paatti applies to it.
    manifest({"type": "rahti-data", "data": {"schema_version": "0.3.0", "entries": []}})
    with pytest.raises(ValidationError, match="major version 0"):
        RahtiDocument.model_validate({"schema_version": "1.0.0", "entries": []})

    # Suola's schema refuses a version in `rules.json`; that is Suola's check, and the model does not look.
    RulesDocument.model_validate({"schema_version": "1.0.0", "sites": []})


def test_ordered_puts_weight_before_document_order():
    parsed = manifest(
        {"type": "rahti-data", "href": "./b.json", "weight": 10},
        {"type": "rahti-data", "href": "./a.json"},
        {"type": "suola-rules", "href": "./rules.json"},
        {"type": "rahti-data", "href": "./c.json", "weight": 10},
    )

    assert [r.href for r in parsed.ordered("rahti-data")] == ["./a.json", "./b.json", "./c.json"]


def test_merge_sites_concatenates_rules_and_last_wins_other_fields():
    link = Link(rel="x-publisher-policy", href="https://www.hs.fi/info/")
    first = Site(name="HS", enabled=True, links=[link], rules=[RULE])
    second = Site(
        name="Helsingin Sanomat", enabled=False, links=[link], rules=[RULE, RULE.model_copy(update={"link": "self"})]
    )

    merged = merge_sites(first, second)

    assert (merged.name, merged.enabled) == ("Helsingin Sanomat", False)
    assert merged.links == [link]
    assert [r.link for r in merged.rules] == ["a", "self"]


def test_merge_sites_removes_duplicates_from_either_list():
    first = Site(name="HS", origins=["https://www.hs.fi/*"], rules=[RULE])
    second = Site(name="HS", origins=["https://www.hs.fi/*", "https://hs.fi/*", "https://hs.fi/*"], rules=[RULE, RULE])

    merged = merge_sites(first, second)

    assert merged.rules == [RULE]
    assert merged.origins == ["https://www.hs.fi/*", "https://hs.fi/*"]


def test_merge_rahti_last_wins_per_signature():
    first = RahtiDocument.model_validate(
        {
            "schema_version": "0.1.0",
            "entries": [
                {"title": "old", "urls": [{"sign": "s1"}]},
                {"title": "kept", "urls": [{"sign": "s2"}]},
            ],
        }
    )
    second = RahtiDocument.model_validate(
        {"schema_version": "0.1.0", "entries": [{"title": "new", "urls": [{"sign": "s1"}]}]}
    )

    assert [e["title"] for e in merge_rahti(first, second).entries] == ["kept", "new"]


def test_combine_merges_fetched_documents_in_order_and_inline_last():
    resource = SuolaRulesResource.model_validate(
        {"type": "suola-rules", "href": ["./a.json", "./b.json"], "rules": {"sites": [{"domain": "inline.fi"}]}}
    )
    fetched = [RulesDocument(sites=[{"domain": "a.fi"}]), RulesDocument(sites=[{"domain": "b.fi"}])]

    combined = resource.combine(fetched)

    assert isinstance(combined, RulesDocument)
    assert [s["domain"] for s in combined.sites] == ["a.fi", "b.fi", "inline.fi"]


def test_inline_sites_default_their_origins():
    resource = SitesResource.model_validate({"type": "site-configs", "sites": {"www.hs.fi": {"name": "HS"}}})

    document = resource.inline()
    assert document is not None and document.origins() == {"https://www.hs.fi/*"}


def test_the_committed_schema_matches_the_models():
    assert Path("docs/manifest.schema.json").read_text(encoding="utf-8") == render_schema() + "\n"


def test_a_rendered_manifest_round_trips():
    parsed = Manifest.model_validate(
        {**MANIFEST, "resources": [*MANIFEST["resources"], {"type": "x-private", "href": "./x.json", "extra": 1}]}
    )

    assert Manifest.model_validate_json(render(parsed)) == parsed
