import logging
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

        if settings.suola_rules:
            inst = Suola(custom_rules=settings.suola_rules)
        else:
            inst = Suola()
        _suola_var.set(inst)

    try:
        return inst(url)
    except Exception as e:
        logger.debug("Error hashing URL %s: %s", url, e)
        raise
