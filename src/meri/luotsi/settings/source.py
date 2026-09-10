"""
Configuration schemas for feedback sources.

The Google Form timestamp column carries no timezone and its format follows the spreadsheet locale, so both are
per-source configuration.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class _Source(BaseModel):
    """Fields shared by every feedback source."""

    timezone: str = Field(default="Europe/Helsinki", description="Timezone the source's timestamps are recorded in.")
    timestamp_format: str = Field(
        default="%m/%d/%Y %H:%M:%S",
        description="`strptime` format of the timestamp column. Google Forms writes it in the spreadsheet locale.",
    )


class GoogleSheets(_Source):
    """Configuration options for pulling feedback from Google Sheets via the Visualization API."""

    type: Literal["sheets"] = "sheets"
    spreadsheet_id: str
    worksheet: str | None = None


class Csv(_Source):
    """Configuration options for pulling feedback from local CSV files."""

    type: Literal["csv"] = "csv"
    path: str


FeedbackSourceConfig = Annotated[
    GoogleSheets | Csv,
    Field(discriminator="type"),
]
