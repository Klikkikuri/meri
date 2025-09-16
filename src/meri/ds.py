"""
Dataset format
==============
"""

from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime
from .abc import ArticleTypeLabels, ClickbaitScale, LinkLabel

class UrlSignature(BaseModel):
    """
    Url format.
    """

    labels: list[LinkLabel] = Field(default_factory=list)
    sign: str = Field(..., description="Url signature hash.")


class HeadlineEntry(BaseModel):
    updated: datetime = Field(default_factory=datetime.now, description="Last update time.")

    urls: list[UrlSignature] = Field([], description="List of URLs.")
    title: str = Field(..., description="Title of the article.")
    clickbaitiness: ClickbaitScale = Field(..., description="Clickbaitiness level.")
    labels: list[ArticleTypeLabels] = Field(default_factory=list)
    updated: datetime = Field(default_factory=datetime.now, description="Last update time.")

class Dataset(BaseModel):
    """
    Dataset format.
    """
    status: str = Field(..., description="Status of the dataset.")
    updated: datetime = Field(default_factory=datetime.now, description="Last update time.")
    schema_version: str = Field(..., description="Version of the dataset schema.")

    entries: list[HeadlineEntry] = Field([], description="List of articles.")


if __name__ == "__main__":
    import json
    from datetime import datetime

    ds = Dataset(
        status="ok",
        schema_version="0.1.0",
        updated=datetime.now(),
        entries=[
            HeadlineEntry(
                title="Example headline",
                clickbaitiness=ClickbaitScale.LOW,
                labels=[
                    ArticleTypeLabels.TYPE_ARTICLE
                ],
                urls=[
                    UrlSignature(
                        sign="abc123",
                        labels=[LinkLabel.LINK_CANONICAL]
                    )
                ]
            )
        ]
    )

    print(json.dumps(ds.dict(), indent=2, default=str))