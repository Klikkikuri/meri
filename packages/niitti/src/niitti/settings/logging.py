import sys
from typing import Literal
from pydantic import BaseModel, Field


def _default_log_format() -> Literal["json", "console"]:
    """Detect interactivity based on TTY status."""
    return "console" if sys.stdout.isatty() else "json"


class LoggingSettings(BaseModel):
    """
    Logging configuration model for niitti logging system.
    """

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        description="Logging level.",
    )

    LOG_FORMAT: Literal["json", "console", "text"] = Field(
        default_factory=_default_log_format,
        description="Log format: console/text (interactive TTY) or json (non-interactive stream).",
    )

    DEBUG: bool = Field(
        False,
        description="Enable debug mode.",
    )
