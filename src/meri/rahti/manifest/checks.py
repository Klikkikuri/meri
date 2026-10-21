"""
Strict authoring checks over a loaded manifest, for `meri manifest validate`.

The models accept what a consumer must tolerate at run time, such as a resource type from a newer version. These
checks find what an author most likely did not intend, and validate each linked document against its own format.
"""

import difflib
from collections.abc import Iterator
from typing import TYPE_CHECKING, NamedTuple

from pydantic import ValidationError
from suola import Suola

from .loader import LoadedResource, is_cross_origin
from .models import (
    RESOURCE_TYPES,
    Manifest,
    RahtiResource,
    SitesDocument,
    SitesResource,
    SuolaRulesResource,
    describe,
)

if TYPE_CHECKING:
    from meri.settings.rahti import RahtiSettings


class Problem(NamedTuple):
    subject: str
    message: str

    def __str__(self) -> str:
        return f"{self.subject}: {self.message}"


def check(
    manifest: Manifest, base: str, loaded: list[LoadedResource], settings: "RahtiSettings | None" = None
) -> list[Problem]:
    """
    Check a manifest and the resources loaded from it.

    :param base: Location of the manifest.
    :param loaded: The result of :func:`~meri.rahti.manifest.loader.load_resources` for all types.
    """
    problems = [*_unknown_types(manifest), *_origins(manifest, base, loaded, settings)]

    suola: Suola | None = None
    for item in loaded:
        subject = describe(item.resource)
        for location, error in item.errors.items():
            problems.append(Problem(subject, f"cannot load {location}: {error}"))

        # Each document is checked as it is written: a merge can hide an invalid entry or drop a field.
        for source, content in _documents(item):
            match item.resource:
                case RahtiResource():
                    # Imported here: `meri.rahti.data` imports `meri.abc`, which imports `meri.suola`, which imports
                    # this module.
                    from ..data import RahtiData

                    try:
                        RahtiData.model_validate_json(content)
                    except ValidationError as e:
                        problems.append(Problem(subject, f"{source} is not valid Rahti data: {e}"))
                case SuolaRulesResource():
                    # Suola is the only authority on its rules: a document it cannot compile is incompatible with it.
                    suola = suola or Suola()
                    try:
                        suola.append_rules(content)
                    except (RuntimeError, ValueError) as e:
                        problems.append(Problem(subject, f"Suola cannot use the rules of {source}: {e}"))
    return problems


def _documents(item: LoadedResource) -> Iterator[tuple[str, bytes]]:
    """The linked documents of a resource as fetched, and then its inline content, each with the place it is from."""
    for location, blob in item.blobs.items():
        yield location, blob.content
    if (inline := item.resource.inline()) is not None:
        yield "the inline content", inline.model_dump_json(by_alias=True).encode()


def _unknown_types(manifest: Manifest) -> Iterator[Problem]:
    for resource in manifest.resources:
        if resource.type in RESOURCE_TYPES or resource.type.startswith("x-"):
            continue
        close = difflib.get_close_matches(resource.type, RESOURCE_TYPES, n=1)
        hint = f", did you mean {close[0]!r}?" if close else ""
        yield Problem(describe(resource), f"unknown resource type {resource.type!r}{hint}")


def _origins(
    manifest: Manifest, base: str, loaded: list[LoadedResource], settings: "RahtiSettings | None"
) -> Iterator[Problem]:
    """A cross-origin `site-configs` href must declare exactly the origins of its document; other ones must not."""
    contents = {id(item.resource): item.content for item in loaded}

    for resource in manifest.ordered("site-configs"):
        assert isinstance(resource, SitesResource)
        subject = describe(resource)
        try:
            cross_origin = any(is_cross_origin(base, href, settings) for href in resource.hrefs)
        except ValueError:
            continue  # The loader reports an href that does not resolve.

        if not cross_origin:
            if resource.origins is not None:
                yield Problem(subject, "declares `origins`, which only a cross-origin `href` may declare")
            continue

        if not resource.origins:
            yield Problem(
                subject, "links a document on another origin, so it must declare the `origins` it contributes"
            )
            continue

        content = contents.get(id(resource))
        if isinstance(content, SitesDocument):
            declared, actual = set(resource.origins), content.origins()
            if missing := actual - declared:
                yield Problem(subject, f"`origins` does not declare {sorted(missing)}, so consumers drop those sites")
            if unused := declared - actual:
                yield Problem(subject, f"`origins` declares {sorted(unused)}, which no site in the document uses")
