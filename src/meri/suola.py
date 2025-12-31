"""
Suola URL hashing utility.
"""
import logging
from pathlib import Path
from pydantic import AnyHttpUrl, HttpUrl
from suola import Suola
from contextvars import ContextVar
from typing import Optional

# replace eager default with lazy-initialized per-context singleton
_suola_var: ContextVar[Optional[Suola]] = ContextVar(f"{__name__}.suola", default=None)

type Url = str | HttpUrl | AnyHttpUrl

logger = logging.getLogger(__name__)


def hash_url(url: Url) -> str | None:
    """
    Hash the given URL using :class:`Suola`.
    """
    url = str(url)
    url = url.strip()

    # Initialize singleton
    inst = _suola_var.get()
    if inst is None:
        from .settings import settings
        inst = init_suola(settings.suola_rules)

    try:
        return inst(url)
    except Exception as e:
        logger.exception("Error hashing URL %s: %s", url, e)
        raise


def init_suola(rules_path: Optional[str | Path] = None) -> Suola:
    """
    Initialize the Suola instance with optional custom rules.

    Returns the initialized Suola instance.
    """
    if rules_path:
        rules_path = Path(rules_path).resolve()
        if not rules_path.is_file():
            raise FileNotFoundError(f"Suola rules file not found: {rules_path}")

    logger.info("Initializing Suola with rules: %s", rules_path or "default rules")
    inst = Suola(custom_rules=rules_path) if rules_path else Suola()
    _suola_var.set(inst)
    return inst
