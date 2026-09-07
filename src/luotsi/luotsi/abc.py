"""
Abstract base classes and data models for Luotsi.

Defines the shape of a reader feedback item and the interfaces that feedback sources and guardrails implement.
"""

import abc
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class FeedbackType(str, Enum):
    """Reader verdict on a generated title."""

    GOOD = "good_conversion"
    BAD = "bad_conversion"
    SUGGESTION = "suggestion"


class Feedback(BaseModel):
    """
    One reader feedback submission.

    Field names are the normalized form of the raw spreadsheet headers (e.g. ``pageUrl`` -> ``page_url``), so all
    sources produce one schema.
    """

    type: FeedbackType = Field(..., description="Reader verdict on the generated title.")
    message: str = Field("", description="Free-text reader comment. Empty when the reader only voted.")
    url_sign: str = Field(..., description="Hash signature of the article URL.")
    submitted_at: datetime | None = Field(None, description="Submission time in UTC. None when unparseable.")
    page_url: str | None = Field(None, description="URL of the page the feedback was submitted from.")
    original_title: str | None = Field(None, description="Original headline of the article.")
    converted_title: str | None = Field(None, description="Generated non-clickbait title the reader rated.")
    clickbait_level: str | None = Field(None, description="Clickbait level the reader saw.")
    database_updated: str | None = Field(None, description="Publication time of the data set the reader saw.")


@dataclass
class FeedbackItem:
    """
    A feedback item as it travels through the guardrail chain.

    Guards see both states: detection guards key off :attr:`original` so that earlier guards cannot strip the
    evidence they look for, while mutating guards write only to :attr:`processed`.
    """

    original: Feedback
    """As received from the source. Guards never mutate this."""

    processed: Feedback
    """Current state. Mutating guards replace or modify this one."""


class FeedbackSource(abc.ABC):
    """Interface for feedback collectors (CSV file, Google Sheet, ...)."""

    @abc.abstractmethod
    def get_feedback(self) -> list[Feedback]:
        """
        Fetch all feedback items the source holds.

        :return: Validated feedback items. Sources log and return an empty list on failure.
        """


class Guardrail(abc.ABC):
    """Interface for one step of the sanitization chain."""

    name: str
    """Short identifier used in log lines."""

    @abc.abstractmethod
    def run(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """
        Filter or modify a batch of feedback items.

        :param items: Items surviving the previous guards.
        :return: The items that pass this guard, with :attr:`FeedbackItem.processed` updated as needed.
        """
