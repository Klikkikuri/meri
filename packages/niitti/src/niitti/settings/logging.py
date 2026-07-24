import sys
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


def _default_log_format() -> Literal["json", "console"]:
    """Detect interactivity based on TTY status."""
    return "console" if sys.stdout.isatty() else "json"


class LoggingSettings(BaseModel):
    """
    Logging configuration model for niitti logging system.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level.",
        validation_alias="LOG_LEVEL",
    )

    LOG_FORMAT: Literal["json", "console", "text"] = Field(
        default_factory=_default_log_format,
        description="Log format: console/text (interactive TTY) or json (non-interactive stream).",
        validation_alias="LOG_FORMAT",
    )

    DEBUG: bool = Field(
        default=False,
        description="Enable debug mode.",
        validation_alias="DEBUG",
    )

    def get_logging_settings(self) -> "LoggingSettings":
        return LoggingSettings(
            LOG_LEVEL=self.LOG_LEVEL,
            LOG_FORMAT=self.LOG_FORMAT,
            DEBUG=self.DEBUG,
        )
