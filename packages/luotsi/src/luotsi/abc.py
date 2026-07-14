"""
Abstract Base Classes and Data Models for Luotsi.

Defines the structure of feedback items retrieved from various sources
and sets the interface protocols for feedback sources and guardrail filters.
"""

import abc
from pydantic import BaseModel, Field
from enum import Enum

class FeedbackType(str, Enum):
    """Supported classifications of user feedback for Title generation."""
    GOOD = "good_conversion"
    BAD = "bad_conversion"
    SUGGESTION = "suggestion"

class Feedback(BaseModel):
    """
    Unified representation of user feedback.

    Fields are mapped from raw spreadsheet headers (e.g. pageUrl -> page_url)
    to enforce a consistent schema across JSON and Google Sheet sources.
    """
    type: FeedbackType = Field(..., description="Type of the feedback, e.g., good_conversion, bad_conversion, suggestion")
    message: str = Field(..., description="Feedback comments or suggestions")
    timestamp: str | None = Field(None, description="Timestamp of the feedback submission")
    page_url: str | None = Field(None, description="URL of the page where feedback was submitted")
    url_sign: str = Field(..., description="Hash signature of the article URL")
    original_title: str | None = Field(None, description="Original headline title of the article")
    converted_title: str | None = Field(None, description="Converted non-clickbait title of the article")
    clickbait_level: str | None = Field(None, description="Level of clickbaitiness rating")
    database_updated: str | None = Field(None, description="Timestamp when the database was updated")

class FeedbackSource(abc.ABC):
    """Interface for feedback collectors (e.g., CSV, JSON, Sheets)."""
    @abc.abstractmethod
    def get_feedback(self, signature: str | None = None) -> list[Feedback]:
        """
        Fetch feedback items from the source.

        Args:
            signature: Optional URL hash signature to narrow down fetched feedback.
        """
        pass

class Guardrail(abc.ABC):
    """Interface for verification/filtering pipeline steps."""
    @abc.abstractmethod
    def filter(self, feedbacks: list[Feedback]) -> list[Feedback]:
        """
        Filter or modify feedback items in place.

        Acts as a layer to reject spam, prompt injections, or malformed messages.
        """
        pass
