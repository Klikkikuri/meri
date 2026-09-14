"""
Suola URL hashing utility.

Suola parses compiled JSON rules only. Meri takes them from the `suola-rules` resources of the provider manifest
that the `manifest` setting names: the first document that loads replaces the rules built into the module, and
each further one is appended. Without a manifest, or when no rules load, the built-in rules are used.
"""
import hashlib
import time
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from niitti import get_logger
from niitti.paths import cache_dir
from pydantic import AnyHttpUrl, HttpUrl
from suola import Suola

from .rahti.manifest.loader import (
    LoadedResource,
    is_local,
    load_resources,
    read_manifest,
)
from .rahti.manifest.models import RulesDocument, describe
from .rahti.storage import FileDriver, StoredBlob, driver_for, write_atomic

if TYPE_CHECKING:
    from .settings.rahti import RahtiSettings

# replace eager default with lazy-initialized per-context singleton
_suola_var: ContextVar[Suola | None] = ContextVar(f"{__name__}.suola", default=None)

type Url = str | HttpUrl | AnyHttpUrl

#: How long a downloaded rules file is reused before refetching. The instance is initialized per context, so
#: without this every new context would hit the network.
RULES_MAX_AGE = timedelta(days=1)

logger = get_logger(__name__)


def hash_url(url: Url) -> str | None:
    """
    Hash the given URL using :class:`Suola`.
    """
    url = str(url)
    url = url.strip()

    with logger.span("hash_url", url=url):
        # Initialize singleton lazily per-context
        inst = _suola_var.get()
        if inst is None:
            from .settings import settings
            inst = init_suola(settings.manifest, settings.rahti)

        try:
            sign = inst(url)

            if not sign:
                logger.debug("Suola returned no signature for URL", url=url)

            return sign
        except Exception as e:
            logger.exception("Error hashing URL", url=url, error=str(e))
            raise


def init_suola(manifest: str | None = None, storage: "RahtiSettings | None" = None) -> Suola:
    """
    Initialize the Suola instance of this context from the `suola-rules` resources of a manifest.

    :param manifest: Location of the manifest. Empty means the built-in rules.
    :param storage: Credentials for reading the manifest and the rules.
    """
    inst = build_suola(load_rules(manifest, storage))
    _suola_var.set(inst)
    return inst


def load_rules(manifest: str | None, storage: "RahtiSettings | None" = None) -> list[LoadedResource]:
    """
    The `suola-rules` resources of `manifest` that load, in precedence order.

    Rules are not worth failing a run over: a manifest that cannot be read, or a document that does not load, is
    logged and skipped.
    """
    if not manifest:
        return []

    try:
        document = read_manifest(manifest, storage)
    except (OSError, ValueError) as e:
        logger.warning("Cannot read the manifest, using built-in Suola rules", manifest=manifest, error=str(e))
        return []

    loaded = load_resources(
        document, manifest, types={"suola-rules"}, settings=storage, read=lambda location: _cached_read(location, storage)
    )
    for item in loaded:
        for location, error in item.errors.items():
            logger.warning(
                "Skipping Suola rules that do not load",
                resource=describe(item.resource),
                location=location,
                error=str(error),
            )
    return [item for item in loaded if item.content is not None]


def build_suola(rules: list[LoadedResource]) -> Suola:
    """A Suola instance with the first rules document as its base, and the others appended in order."""
    inst: Suola | None = None
    for item in rules:
        try:
            if inst is None:
                inst = Suola(custom_rules=_rules_file(item))
            else:
                inst.append_rules(_rules_bytes(item))
            logger.info("Loaded Suola rules", resource=describe(item.resource), locations=list(item.blobs))
        # The Wasm runtime raises its own error types for rules it refuses, and they are not part of Suola's API.
        except Exception as e:  # noqa: BLE001
            logger.warning("Suola refused rules, skipping them", resource=describe(item.resource), error=str(e))

    if inst is None:
        logger.info("Initializing Suola with built-in rules")
        return Suola()
    return inst


def _rules_bytes(item: LoadedResource) -> bytes:
    assert item.content is not None
    return item.content.model_dump_json().encode("utf-8")


def _rules_file(item: LoadedResource) -> Path:
    """
    A rules file on disk, as the Wasm runtime preopens its parent directory.

    A single local document with no inline content is used as it is; merged content is written to the cache.
    """
    if len(item.blobs) == 1 and item.resource.inline() is None and is_local(location := next(iter(item.blobs))):
        return FileDriver.path(location)

    content = _rules_bytes(item)
    # Named by content, so concurrent contexts never write different rules to one file.
    path = _cache_dir() / f"rules-{hashlib.sha256(content).hexdigest()[:16]}.json"
    if not path.is_file():
        write_atomic(path, content)
    return path


def _cache_dir() -> Path:
    return cache_dir(__package__ or "meri") / "suola"


def _rules_cache(location: str) -> Path:
    """The cached copy of the rules document at a remote `location`."""
    return _cache_dir() / f"{hashlib.sha256(location.encode()).hexdigest()[:16]}.json"


def _cached_read(location: str, storage: "RahtiSettings | None" = None) -> StoredBlob:
    """
    Read a rules document, and keep a copy of a remote one for :data:`RULES_MAX_AGE`.

    When a refresh fails, a stale copy is used; with no copy, the error is raised and the resource is skipped.
    """
    driver = driver_for(location, storage)
    if isinstance(driver, FileDriver):
        return driver.read(location)

    cache = _rules_cache(location)
    try:
        age = timedelta(seconds=time.time() - cache.stat().st_mtime) if cache.is_file() else None
    except OSError:
        age = None

    if age is not None and age < RULES_MAX_AGE:
        logger.debug("Using cached Suola rules", location=location, path=str(cache), age=str(age))
        return StoredBlob(cache.read_bytes(), None)

    try:
        blob = driver.read(location)
        # Guard against caching an error page or a truncated response.
        RulesDocument.model_validate_json(blob.content)
        write_atomic(cache, blob.content)
        return blob
    except (OSError, ValueError) as e:
        if cache.is_file():
            logger.warning("Could not refresh Suola rules, using cached copy", location=location, error=str(e))
            return StoredBlob(cache.read_bytes(), None)
        raise
