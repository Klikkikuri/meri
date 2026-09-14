"""
Read a provider manifest and the documents that its resources link to.
=======================================================================

This is the only module of :mod:`meri.rahti.manifest` that does I/O, and it reads through
:mod:`meri.rahti.storage`. A document that does not load is recorded with its error, and the documents that load
still merge, so one bad document does not stop the others.
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlsplit

from pydantic import BaseModel

from ..storage import MAX_DOCUMENT_SIZE, Driver, FileDriver, StoredBlob, driver_for
from .models import Manifest, Resource

if TYPE_CHECKING:
    from meri.settings.rahti import RahtiSettings

#: Most documents one manifest may cause to be read. Each href spends one.
MAX_FETCHES = 32

type Reader = Callable[[str], StoredBlob]


class LoadedResource(NamedTuple):
    resource: Resource
    blobs: dict[str, StoredBlob]
    """The documents that loaded, keyed by resolved href, in list order."""
    content: BaseModel | None
    """The loaded documents in list order, then the inline content, merged by the rule of the type. `None` if empty."""
    errors: dict[str, Exception]
    """The hrefs that did not load, keyed by resolved href, or by the href as written when it does not resolve."""


def read_manifest(location: str, settings: "RahtiSettings | None" = None) -> Manifest:
    """Read and validate the manifest at `location`. Nothing that it links to is read."""
    return Manifest.model_validate_json(driver_for(location, settings).read(location).content)


def resolve_href(base: str, href: str, settings: "RahtiSettings | None" = None) -> str:
    """Resolve `href` against the document at `base`, in the terms of the driver for `base`."""
    return driver_for(base, settings).join(base, href)


def is_local(location: str) -> bool:
    return FileDriver.matches(location)


def origin_of(location: str) -> tuple[str, str]:
    """The `(scheme, host)` origin of a location. All local files share one origin."""
    if is_local(location):
        return ("file", "")
    parts = urlsplit(location)
    return (parts.scheme, parts.netloc)


def is_cross_origin(base: str, href: str, settings: "RahtiSettings | None" = None) -> bool:
    """:raises ValueError: If `href` does not resolve against `base`."""
    return origin_of(resolve_href(base, href, settings)) != origin_of(base)


def load_resources(
    manifest: Manifest,
    base: str,
    *,
    types: Iterable[str] | None = None,
    settings: "RahtiSettings | None" = None,
    read: Reader | None = None,
) -> list[LoadedResource]:
    """
    Load the resources of the given `types`, in precedence order.

    :param base: Location of the manifest, which relative hrefs resolve against.
    :param types: Resource types to load. Other resources are not read. All registered types when `None`.
    :param read: Reads one location. Defaults to the storage driver for it.
    """
    drivers: dict[type[Driver], Driver] = {}

    def read_with_driver(location: str) -> StoredBlob:
        # One driver of each kind for the whole resolution, so its HTTP session and connections are reused.
        driver = driver_for(location, settings)
        return drivers.setdefault(type(driver), driver).read(location)

    read = read or read_with_driver
    cache: dict[str, StoredBlob] = {}
    fetches = 0

    def fetch(location: str) -> StoredBlob:
        nonlocal fetches
        if location in cache:
            return cache[location]
        if is_local(location) and not is_local(base):
            raise ValueError(f"A remote manifest cannot link a local file: {location}")
        if fetches >= MAX_FETCHES:
            raise ValueError(f"The manifest has used all of its {MAX_FETCHES} fetches")
        fetches += 1
        blob = read(location)
        if len(blob.content) > MAX_DOCUMENT_SIZE:
            raise ValueError(f"Document is larger than {MAX_DOCUMENT_SIZE} bytes: {location}")
        cache[location] = blob
        return blob

    loaded = []
    for resource in manifest.ordered(*(types or ())):
        blobs: dict[str, StoredBlob] = {}
        documents: list[BaseModel] = []
        errors: dict[str, Exception] = {}
        for href in resource.hrefs:
            location = href
            try:
                location = resolve_href(base, href, settings)
                blob = fetch(location)
                documents.append(resource.document.model_validate_json(blob.content))
                blobs[location] = blob
            except (OSError, ValueError) as e:
                # `requests` errors are `OSError`s, and pydantic's `ValidationError` is a `ValueError`.
                errors[location] = e

        loaded.append(LoadedResource(resource, blobs, resource.combine(documents), errors))
    return loaded
