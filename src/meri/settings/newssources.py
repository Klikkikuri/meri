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
    # extractor: Optional[str] = Field(
    #     None,
    #     description="Article content extractor (auto-matched by URL if not specified)",
    # )

    enabled: bool = Field(True, description="Enable the news source.")

    language: Optional[str] = Field(
        None,
        description="Default language of the news source content, e.g., 'fi-FI' for Finnish.",
    )

    # Rules to filter articles from this source
    min_content_length: Optional[int] = Field(
        300,
        description="Minimum content length (in characters) for fetched articles. Articles with less content will be ignored.",
    )
    max_num_articles: Optional[int] = Field(
        100,
        description="Maximum number of articles to fetch from this source during each fetch operation.",
    )
    max_age_days: Optional[int] = Field(
        7 * 3,
        description="Maximum age of articles (in days) to fetch from this source. Articles older than this will be ignored.",
    )

    # weight: Optional[int] = Field(50, description="Weight of the news source. Higher weight means higher priority.")

    # Convert single URL to list
    @field_validator("url", mode="before")
    @classmethod
    def url_to_list(cls, v):
        if not isinstance(v, list):
            return [v]
        return v
