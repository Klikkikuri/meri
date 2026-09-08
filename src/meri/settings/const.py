from typing import Final

DEFAULT_BOT_ID: Final = "Klikkikuri-Meri-Bot"
PKG_NAME: Final = str(__package__ or "meri")
APP_NAME: Final = "meri"
"""Application name for the XDG directories: data and cache belong to `meri`, not to one of its submodules."""
DEFAULT_BOT_USER_AGENT: Final = r"Mozilla/5.0 (compatible; {BOT_ID}/{Version}; +https://github.com/Klikkikuri/meri/docs/meribot.md)"
