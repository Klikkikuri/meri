"""Configuration schema for Luotsi."""

from pydantic import BaseModel, Field

from .source import Csv, FeedbackSourceConfig, GoogleSheets

__all__ = ["Csv", "FeedbackSourceConfig", "GoogleSheets", "LuotsiSettings"]


class LuotsiSettings(BaseModel):
    """
    Reader feedback settings.

    Sources default to empty so that a host application can embed the model without configuring Luotsi.
    """

    sources: list[FeedbackSourceConfig] = Field(default_factory=list, description="Feedback sources to pull from.")
