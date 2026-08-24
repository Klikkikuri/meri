"""
Suola URL hashing utility.

Suola parses compiled JSON rules only; the `rules.yaml` in the suola repository is build-time source that
`make rules` compiles. Rules are therefore consumed from a *location* – an `http(s)://` URL, a `file://` URL
or a filesystem path – which :func:`resolve_rules` turns into a local JSON file for the Wasm runtime.
"""
import json
import os
import time
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path

import requests
from niitti import get_logger
from platformdirs import user_cache_dir
from pydantic import AnyHttpUrl, HttpUrl
from suola import Suola

# replace eager default with lazy-initialized per-context singleton
_suola_var: ContextVar[Suola | None] = ContextVar(f"{__name__}.suola", default=None)

type Url = str | HttpUrl | AnyHttpUrl

#: How long a downloaded rules file is reused before refetching. The instance is initialized per context, so
#: without this every new context would hit the network.
RULES_MAX_AGE = timedelta(days=1)

RULES_TIMEOUT = 30

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
            inst = init_suola(settings.suola_rules)

        try:
            sign = inst(url)

            if not sign:
                logger.debug("Suola returned no signature for URL", url=url)

            return sign
        except Exception as e:
            logger.exception("Error hashing URL", url=url, error=str(e))
            raise


def init_suola(rules: str | Path | None = None) -> Suola:
    """
    Initialize the Suola instance with optional custom rules.

    :param rules: Rules location, see :func:`resolve_rules`.
    :return: The initialized Suola instance.
    """
    rules_path = resolve_rules(rules)

    logger.info("Initializing Suola with rules", rules=str(rules_path) if rules_path else "built-in rules")
    inst = Suola(custom_rules=rules_path) if rules_path else Suola()
    _suola_var.set(inst)
    return inst


def assert_json_rules(location: str | Path) -> None:
    """
    Reject YAML rule locations, which Suola has not accepted since v0.5.0.

    :raises ValueError: If the location points to a YAML file.
    """
    if Path(str(location)).suffix.lower() in (".yaml", ".yml"):
        raise ValueError(
            f"Suola rules must be compiled JSON, got a YAML location: {location!s}. Use the published "
            "`rules.json`, or compile a local `rules.yaml` with `make rules` in the suola repository."
        )


def resolve_rules(location: str | Path | None) -> Path | None:
    """
    Resolve a rules location into a local compiled JSON file.

    :param location: An `http(s)://` URL, a `file://` URL or a filesystem path. Empty means built-in rules.
    :return: Path to a local rules file, or `None` to use the rules built into the Suola module.
    :raises ValueError: If the location is YAML.
    :raises FileNotFoundError: If a local location does not exist.
    """
    if not location:
        return None

    location = str(location).strip()
    if not location:
        return None

    assert_json_rules(location)

    if location.startswith(("http://", "https://")):
        return _fetch_rules(location)

    rules_path = Path(location.removeprefix("file://")).resolve()
    if not rules_path.is_file():
        raise FileNotFoundError(f"Suola rules file not found: {rules_path}")
    return rules_path


def _rules_cache() -> Path:
    """Local copy of the downloaded rules. Suola needs a file on disk, as the Wasm runtime preopens its parent."""
    return Path(user_cache_dir(__package__), "rules.json")


def _fetch_rules(url: str) -> Path | None:
    """
    Download rules into the local cache, falling back to the built-in rules when unavailable.

    :return: Path to the cached rules, or `None` when neither the download nor a cached copy is available.
    """
    cache = _rules_cache()

    try:
        age = timedelta(seconds=time.time() - cache.stat().st_mtime) if cache.is_file() else None
    except OSError:
        age = None

    if age is not None and age < RULES_MAX_AGE:
        logger.debug("Using cached Suola rules", path=str(cache), age=str(age))
        return cache

    try:
        from .settings import settings

        response = requests.get(url, headers={"User-Agent": settings.BOT_USER_AGENT}, timeout=RULES_TIMEOUT)
        response.raise_for_status()

        # Guard against caching an error page or a truncated response.
        if "sites" not in json.loads(response.content):
            raise ValueError("Rules document has no 'sites' key")

        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".json.tmp")
        tmp.write_bytes(response.content)
        os.replace(tmp, cache)

        logger.info("Downloaded Suola rules", url=url, path=str(cache))
        return cache
    except (requests.RequestException, OSError, ValueError) as e:
        # Rules are not worth failing the run over: a transport, payload or cache-write problem degrades to
        # the previously cached rules, and failing that to the ones built into the module.
        if cache.is_file():
            logger.warning("Could not refresh Suola rules, using cached copy", url=url, path=str(cache), error=str(e))
            return cache

        logger.warning("Could not download Suola rules, using built-in rules", url=url, error=str(e))
        return None
