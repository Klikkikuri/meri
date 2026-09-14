"""
`meri manifest`: generate, validate and show the provider manifest, and print its JSON schema.

See `docs/provider-manifest.md` for the format.
"""

import json
import sys
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from ..rahti.manifest.checks import check
from ..rahti.manifest.loader import (
    is_cross_origin,
    is_local,
    load_resources,
    read_manifest,
    resolve_href,
)
from ..rahti.manifest.models import (
    RESOURCE_TYPES,
    SCHEMA_URL,
    SCHEMA_VERSION,
    Link,
    Manifest,
    Resource,
    SitesDocument,
    SitesResource,
    UnknownResource,
    describe,
    manifest_schema,
)
from ..rahti.storage import BlobNotFoundError, driver_for
from ..settings.settings import Settings

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

DEFAULT_PATH = "packages/rahti/manifest.json"
DEFAULT_NAME = "Klikkikuri"

#: The conventional document of each type, next to the manifest.
CONVENTIONAL_HREFS = {"suola-rules": "./rules.json", "rahti-data": "./data.json", "site-configs": "./sites.json"}


@click.group("manifest")
def cli() -> None:
    """Generate, validate and show the provider manifest."""


def render(manifest: Manifest) -> bytes:
    """The manifest as it is written: `$schema` first, and no field that holds its default."""
    data = manifest.model_dump(mode="json", by_alias=True, exclude_defaults=True, exclude={"schema_"})
    return (json.dumps({"$schema": SCHEMA_URL, **data}, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def render_schema() -> str:
    return json.dumps(manifest_schema(), indent=2, ensure_ascii=False)


def _location(settings: Settings, location: str | None) -> str:
    location = location or settings.manifest
    if not location:
        raise click.UsageError("No manifest: give a LOCATION, or set `manifest`.")
    return location


def _read(settings: Settings, location: str) -> Manifest:
    try:
        return read_manifest(location, settings.rahti)
    except (OSError, ValueError) as e:
        raise click.ClickException(f"Cannot read manifest {location}: {e}") from e


@cli.command()
@click.argument("location", required=False)
@click.pass_context
def validate(ctx: click.Context, location: str | None) -> None:
    """
    Check a manifest and every document that it links to.

    Prints each problem, and exits non-zero if there is one. LOCATION defaults to the `manifest` setting.
    """
    settings: Settings = ctx.obj["settings"]
    location = _location(settings, location)
    manifest = _read(settings, location)

    problems = check(manifest, location, load_resources(manifest, location, settings=settings.rahti), settings.rahti)
    for problem in problems:
        click.echo(str(problem), err=True)
    if problems:
        ctx.exit(1)
    click.echo(f"{location}: valid")


@cli.command()
@click.argument("location", required=False)
@click.pass_context
def show(ctx: click.Context, location: str | None) -> None:
    """
    Print the resolved manifest as JSON: the content of each resource type, merged in precedence order.

    A document that does not load is reported and left out. LOCATION defaults to the `manifest` setting.

    This is not what a consumer applies: no consumer safety rule, such as declared scope, shadowing or the declared
    `origins` of a cross-origin `site-configs` resource, filters it. `validate` reports origins that disagree.
    """
    settings: Settings = ctx.obj["settings"]
    location = _location(settings, location)
    manifest = _read(settings, location)

    merged: dict[str, BaseModel] = {}
    for item in load_resources(manifest, location, settings=settings.rahti):
        for failed, error in item.errors.items():
            click.echo(f"{describe(item.resource)}: cannot load {failed}: {error}", err=True)
        if item.content is None:
            continue
        kind = item.resource.type
        merged[kind] = type(item.resource).merge(merged[kind], item.content) if kind in merged else item.content

    resolved = {
        kind: content.model_dump(mode="json", by_alias=True, exclude_none=True) for kind, content in merged.items()
    }
    click.echo(json.dumps(resolved, indent=2, ensure_ascii=False))


@cli.command()
def schema() -> None:
    """Print the JSON schema of the manifest, as committed in `docs/manifest.schema.json`."""
    click.echo(render_schema())


def _is_terminal() -> bool:
    return sys.stdin.isatty()


def _new_resource(settings: Settings, path: str, kind: str, href: str) -> Resource:
    resource = RESOURCE_TYPES[kind].model_validate({"type": kind, "href": href})
    if isinstance(resource, SitesResource) and is_cross_origin(path, href, settings.rahti):
        # The consumer must see the sites of a cross-origin document before it is fetched, so declare them here.
        location = resolve_href(path, href, settings.rahti)
        document = SitesDocument.model_validate_json(driver_for(location, settings.rahti).read(location).content)
        resource.origins = sorted(document.origins())
    return resource


def _resources(
    settings: Settings, path: str, kind: str, hrefs: tuple[str, ...], existing: Manifest | None, interactive: bool
) -> list[Resource]:
    if hrefs:
        return [_new_resource(settings, path, kind, href) for href in hrefs]

    current: list[Resource] = [r for r in existing.resources if r.type == kind] if existing else []
    fallback = current or [_new_resource(settings, path, kind, CONVENTIONAL_HREFS[kind])]
    # A single plain href can be asked for; a longer or richer list is kept as it is written.
    if interactive and len(fallback) == 1 and isinstance(fallback[0].href, str):
        answer = click.prompt(f"`{kind}` href", default=fallback[0].href)
        if answer != fallback[0].href:
            return [_new_resource(settings, path, kind, answer)]
    return fallback


def _parse_links(values: tuple[str, ...]) -> list[Link]:
    links = []
    for value in values:
        rel, separator, href = value.partition("=")
        try:
            if not separator:
                raise ValueError("expected REL=HREF")
            links.append(Link(rel=rel, href=href))
        except ValueError as e:
            raise click.BadParameter(f"{value!r}: {e}", param_hint="--link") from e
    return links


@cli.command()
@click.argument("path", required=False)
@click.option("--name", help="Name of the provider.")
@click.option("--suola-rules", "suola_rules", multiple=True, metavar="URL", help="Suola rules document. Repeatable.")
@click.option("--rahti-data", "rahti_data", multiple=True, metavar="URL", help="Rahti data document. Repeatable.")
@click.option("--site-configs", "site_configs", multiple=True, metavar="URL", help="Site configurations. Repeatable.")
@click.option("--link", "links", multiple=True, metavar="REL=HREF", help="Link of the provider. Repeatable.")
@click.option("--no-input", is_flag=True, help="Never prompt.")
@click.pass_context
def generate(
    ctx: click.Context,
    path: str | None,
    name: str | None,
    suola_rules: tuple[str, ...],
    rahti_data: tuple[str, ...],
    site_configs: tuple[str, ...],
    links: tuple[str, ...],
    no_input: bool,
) -> None:
    """
    Write a manifest that links the Suola rules, the Rahti data and the site configurations.

    Each value comes from an option; else from a prompt, on a terminal and without --no-input; else from the
    manifest already at PATH; else from the conventional name next to the manifest, such as `./rules.json`. Only
    the manifest is written, and `updated` changes only when its content changes. PATH may be a GitHub URL, and
    defaults to the `manifest` setting when that is local.
    """
    settings: Settings = ctx.obj["settings"]
    path = path or (settings.manifest if settings.manifest and is_local(settings.manifest) else DEFAULT_PATH)
    driver = driver_for(path, settings.rahti)
    interactive = not no_input and _is_terminal()

    try:
        blob = driver.read(path)
        existing = Manifest.model_validate_json(blob.content)
    except BlobNotFoundError:
        blob, existing = None, None
    except ValidationError as e:
        raise click.ClickException(f"The manifest at {path} is not valid, so it cannot give defaults: {e}") from e

    if not name:
        default = existing.name if existing else DEFAULT_NAME
        name = click.prompt("Provider name", default=default) if interactive else default

    resources = [
        r
        for kind in CONVENTIONAL_HREFS
        for r in _resources(
            settings,
            path,
            kind,
            {"suola-rules": suola_rules, "rahti-data": rahti_data, "site-configs": site_configs}[kind],
            existing,
            interactive,
        )
    ]
    # Resources of types this version does not know are kept as they are written.
    resources += [r for r in existing.resources if isinstance(r, UnknownResource)] if existing else []

    manifest = Manifest.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "name": name,
            "updated": existing.updated if existing else None,
            "links": _parse_links(links) or (existing.links if existing else []),
            "resources": resources,
        }
    )
    if existing is None or render(manifest.model_copy(update={"updated": None})) != render(
        existing.model_copy(update={"updated": None})
    ):
        manifest.updated = datetime.now(UTC).replace(microsecond=0)

    content = render(manifest)
    if blob is not None and blob.content == content:
        click.echo(f"{path}: unchanged")
        return

    driver.write(path, content, version=blob.version if blob else None, message="Update provider manifest")
    click.echo(f"{path}: written")
