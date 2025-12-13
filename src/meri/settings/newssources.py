from typing import Optional

import pydantic_core
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

type Url = str | pydantic_core.Url

# TODO: Discover automatically available discoverers
discover_type = Literal["rss"] | str


class NewsSource(BaseModel):
    """
    Configuration for a news source.

    One news source may have multiple URLs to discover articles from, but only one type.
    """

    name: Optional[str] = Field(
        description="Name of the news source. Used for logging and identification."
    )
    url: list[Url] = Field(..., description="URLs where to discover articles (RSS, API, homepage)")

    type: discover_type = Field(
        default="rss",
        description="Discoverer type to use for this news source. Defaults to 'rss'."
    )
    extractor: Optional[str] = Field(
        None,
        description="Article content extractor (auto-matched by URL if not specified)",
    )

    enabled: bool = Field(True, description="Enable the news source.")

    language: Optional[str] = Field(
        None,
        description="Default language of the news source content, e.g., 'fi-FI' for Finnish.",
    )

    # weight: Optional[int] = Field(50, description="Weight of the news source. Higher weight means higher priority.")

    # Convert single URL to list
    @field_validator("url", mode="before")
    @classmethod
    def url_to_list(cls, v):
        if not isinstance(v, list):
            return [v]
        return v
