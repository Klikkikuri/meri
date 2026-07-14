"""
Configuration schemas for Luotsi.

Utilizes Pydantic to validate settings loaded from Meri configuration files,
allowing multiple feedback source types (Sheets and CSV) to be specified.
"""

from typing import Annotated, Literal
from pydantic import BaseModel, Field

class _LuotsiFeedbackSource(BaseModel):
    """Base schema for feedback sources configuration."""
    pass

class LuotsiFeedbackSourceSheets(_LuotsiFeedbackSource):
    """Configuration options for pulling feedback from Google Sheets via Visualization API."""
    type: Literal["sheets"] = "sheets"
    spreadsheet_id: str
    worksheet: str | None = None

class LuotsiFeedbackSourceCsv(_LuotsiFeedbackSource):
    """Configuration options for pulling feedback from local CSV files."""
    type: Literal["csv"] = "csv"
    path: str

LuotsiFeedbackSource = Annotated[
    LuotsiFeedbackSourceSheets | LuotsiFeedbackSourceCsv,
    Field(discriminator="type"),
]

class LuotsiConfig(BaseModel):
    """
    Configuration settings for Luotsi.

    Defaults sources to an empty list to avoid validation failures in Meri
    when Luotsi is not explicitly configured.
    """
    sources: list[LuotsiFeedbackSource] = Field(default_factory=list)
