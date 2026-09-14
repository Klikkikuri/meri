"""
Rahti data: the `data.json` models, and pulling and pushing them through a storage driver.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from niitti import get_logger
from pydantic import BaseModel, Field, field_validator

from ..abc import ArticleLabels, ArticleTypeLabels, ClickbaitScale, LinkLabel
from .storage import BlobNotFoundError, FileDriver, driver_for

if TYPE_CHECKING:
    from meri.settings.rahti import RahtiSettings

COMMIT_MESSAGE = r"""
{# HACK: Old legacy entries in data.json or aggressively filtered articles may occasionally have an empty urls list, even though this should theoretically never happen. We safeguard all url index accesses to prevent pipeline crashes. #}
[🤖 bot]: Updated list with {{articles | length}} additions or updates, and removed {{removed | length}} old entries.
{% if processed %}
New or updated entries:
{% for item in processed %}
- {{ item.article.href.signature[0:7] if item.article.href else '???????' }}: {{item.title.title | truncate(68)}}

  {{item.title.contemplator | wordwrap(77) | indent(2)}}
{% endfor %}
{% endif %}

{% if unprocessed %}
Identified but unprocessed entries:
{% for item in unprocessed %}
- {{ item.article.href.signature[0:7] if item.article.href else '???????' }} [reason: {{item.skip_reason}}]
{% endfor %}
{% endif %}

{% if removed %}
Removed entries:
{% for entry in removed %}
- {{ (entry.urls|first).sign if entry.urls else '???????' }}
{% endfor %}
{% endif %}
""".strip()

#: Where Rahti data is kept when `rahti.url` is not set.
DEFAULT_LOCATION = "file:///app/instance/rahti/data.json"

logger = get_logger(__name__)

class RahtiUrl(BaseModel):
    sign: str
    labels: list[LinkLabel]


class RahtiEntry(BaseModel):
    updated: datetime = Field(
        description="TZ Aware datetime (ISO 8601) when article was updated"
    )

    urls: list[RahtiUrl] = Field(default_factory=list,
        description="List of URLs associated with the article"
    )
    title: str | None = Field(default=None, description="Generated title for the article")
    clickbaitiness: ClickbaitScale | None = Field(default=None, description="Clickbaitiness score")
    labels: list[ArticleLabels | ArticleTypeLabels] = Field(default_factory=list, description="Labels associated with article")

    # Temporary field to track source of the article. Maps to :py:class:`meri.settings.sources.name`
    outlet: str | None = Field(
        default=None,
        description="Name of the news outlet or source of the article",
    )

    @field_validator("urls")
    @classmethod
    def check_urls_unique(cls, v):
        # Sort urls by most labels
        urls = sorted(v, key=lambda x: len(x.labels), reverse=True)
        seen_signs = set()
        unique_urls = []
        for url in urls:
            if url.sign not in seen_signs:
                unique_urls.append(url)
                seen_signs.add(url.sign)
        return unique_urls


class RahtiData(BaseModel):
    # Metadata
    status: Literal["ok"] = "ok"
    schema_version: Literal["0.1.0"] = "0.1.0"
    updated: datetime = Field(
        description="Datetime (ISO 8601) representing time when list was generated"
    )

    # Data
    entries: list[RahtiEntry]

    @classmethod
    def empty(cls) -> "RahtiData":
        return cls(updated=datetime.now(UTC), entries=[])


class Rahti:
    """
    Pull and push Rahti data at one location, through the storage driver for it.
    """

    def __init__(self, location: str, settings: "RahtiSettings | None" = None) -> None:
        self.location = location
        self.driver = driver_for(location, settings)

    def pull(self) -> tuple[str | None, RahtiData]:
        """
        :return: The version of the stored data, to give back to :meth:`push`, and the data.
        :raises BlobNotFoundError: If a remote location holds no data. Only a missing local file reads as empty
            data: a remote 404 can also mean a wrong token, and pushing over it would lose the data set.
        """
        try:
            content, version = self.driver.read(self.location)
        except BlobNotFoundError:
            if not isinstance(self.driver, FileDriver):
                raise
            logger.warning("Rahti file not found, starting from empty data", location=self.location)
            return None, RahtiData.empty()

        return version, RahtiData.model_validate_json(content)

    def push(self, version: str | None, data: RahtiData, commit_message: str) -> str | None:
        """Store `data` over the `version` that :meth:`pull` returned, and return the new version."""
        content = data.model_dump_json(indent=2).encode("utf-8")
        return self.driver.write(self.location, content, version=version, message=commit_message)


def create_rahti(settings: "RahtiSettings") -> Rahti:
    """
    The Rahti store of a run, at `rahti.url`, or :data:`DEFAULT_LOCATION` when that is not set.

    The manifest never decides this location: a manifest tells consumers what to read, and the operator of each
    Meri instance decides where that instance writes.

    :raises ValueError: If the location is read only, such as a plain `https://` URL.
    """
    location = settings.url or DEFAULT_LOCATION
    if not driver_for(location, settings).writable:
        raise ValueError(f"`rahti.url` {location} is read only. Use a local path or a GitHub URL.")
    return Rahti(location, settings)
