"""
Logging formatter utilities for resolving log levels and formatting log records.
"""

import logging


def _resolve_log_level(level_name: str, default: int = logging.INFO) -> int:
    """
    Resolve a log level name (e.g. "INFO") to its numeric logging level using
    the standard library's own level registry, instead of a manually
    maintained enumeration that can drift out of sync with `logging`'s actual
    levels (e.g. if a custom level is registered via `logging.addLevelName`).

    :param level_name: Level name, case-insensitive (e.g. "debug", "INFO").
    :param default: Fallback level to use if level_name isn't a known level.
    :return: Numeric logging level.
    """
    name = level_name.upper()

    level_names_mapping = getattr(logging, "getLevelNamesMapping", None)
    if level_names_mapping is not None:
        # Python 3.11+: dedicated, unambiguous name -> level API.
        return level_names_mapping().get(name, default)

    # Older Python: logging.getLevelName() doubles as a name -> level lookup
    # for registered level names. This is long-standing, still-supported
    # stdlib behavior (just superseded by getLevelNamesMapping on 3.11+).
    resolved = logging.getLevelName(name)
    return resolved if isinstance(resolved, int) else default
