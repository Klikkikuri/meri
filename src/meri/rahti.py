import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Protocol

import requests
from pydantic import BaseModel, Field, field_validator

from meri.settings import settings

from .abc import ArticleLabels, ArticleTypeLabels, ClickbaitScale, LinkLabel
from .settings.rahti import RahtiFileSettings, RahtiGithubSettings, RahtiSettings

COMMIT_MESSAGE = r"""
[🤖 bot]: Updated list with {{articles | length}} additions or updates, and removed {{removed | length}} old entries.
{% if titles %}
New or updated entries:
{% for entry in articles %}
- {{entry.urls[0].signature[0:7]}}: {{titles[loop.index0].title | truncate(68)}}

  {{titles[loop.index0].contemplator | wordwrap(77) | indent(2)}}
{% endfor %}
{% endif %}

{% if removed %}
Removed entries:
{% for entry in removed %}
- {{entry.urls[0].sign[0:7]}}: {{entry.title | truncate(68)}}
{% endfor %}
{% endif %}
""".strip()

logger = logging.getLogger(__name__)

class RahtiUrl(BaseModel):
    sign: str
    labels: List[LinkLabel]


class RahtiEntry(BaseModel):
    updated: datetime = Field(
        description="TZ Aware datetime (ISO 8601) when article was updated"
    )

    urls: List[RahtiUrl] = Field(default_factory=list,
        description="List of URLs associated with the article"
    )
    title: str = Field(default="Generated title for the article") 
    clickbaitiness: ClickbaitScale
    labels: List[ArticleLabels | ArticleTypeLabels]
    
    # Temporary field to track source of the article. Maps to :py:class:`meri.settings.sources.name`
    outlet: str | None = Field(
        None,
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
        description="Datetime (ISO 8601) representing time when list was generated"
    )

    # Data
    entries: List[RahtiEntry]


class RahtiProtocol(Protocol):
    def pull(self) -> tuple[str, RahtiData]: ...
    def push(self, hash_of_stored_file: str, data: RahtiData, commit_message: str): ...


class RahtiFile(RahtiProtocol):
    """
    Implementation of RahtiProtocol for file-based storage, for testing and local use.
    """
    def __init__(self, settings: RahtiFileSettings) -> None:
        self.settings: RahtiFileSettings = settings

    @property
    def path(self) -> Path:
        # Remove scheme from URL
        _path = self.settings.url.removeprefix("file://")
        return Path(_path).resolve()

    def pull(self) -> tuple[str, RahtiData]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = RahtiData.model_validate_json(f.read())
        except FileNotFoundError:
            logger.warning("Rahti file %r not found, returning empty data", self.path)
            # Return empty data
            data = RahtiData(
                status="ok",
                schema_version="0.1.0",
                updated=datetime.now(timezone.utc),
                entries=[],
            )

        return str(self.path), data

    def push(self, hash_of_stored_file: str, data: RahtiData, commit_message: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(data.model_dump_json(indent=2))

        if settings.DEBUG:
            print(commit_message)

class RahtiRepo(RahtiProtocol):
    """
    Interact with remote rahti data.

    """ 
    settings: RahtiGithubSettings
    _session: requests.Session

    def __init__(self, settings: RahtiGithubSettings) -> None:
        self.settings = settings
        self._session = requests.Session()

        if not self.settings.auth_token:
            logger.warning("Rahti GitHub token not set.") 

        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def pull(self) -> tuple[str, RahtiData]:
        target_url = str(self.settings.url)
        auth_token = self.settings.auth_token.get_secret_value()

        res = self._session.get(
            target_url,
            headers={
                "Authorization": f"Bearer {auth_token!s}",
            },
            timeout=30
        )

        if not res.ok:
            logger.error("Failed to fetch Rahti data: %s %s", res.status_code, res.text)

        res.raise_for_status()

        # TODO: File might not exists, but not considered for now.

        data = res.json()
        match data:
            case {"content": encoded_content, "sha": sha}:
                decoded_data = base64.b64decode(encoded_content)
                data = RahtiData.model_validate_json(decoded_data)
            case _:
                raise RuntimeError("Responded rahti data does not contain expected fields")

        return sha, data

    def push(self, hash_of_stored_file: str, data: RahtiData, commit_message: str):
        """
        Add the result data of a processing run into the existing Rahti storage.
        """
        json_str = data.model_dump_json(indent=2)
        encoded_file_content = base64.b64encode(bytes(json_str, encoding="utf-8")).decode("utf-8")
        auth_token = self.settings.auth_token.get_secret_value()
        res = self._session.put(
            str(self.settings.url),
            headers={
                "Authorization": f"Bearer {auth_token!s}",
            },
            json={
                "message": commit_message,
                "committer": {
                    "name": self.settings.committer.name,
                    "email": self.settings.committer.email
                },
                "content": encoded_file_content,
                "sha": hash_of_stored_file,
            },
            timeout=30
        )

        if not res.ok:
            logger.error("Failed to push Rahti data: %s %s", res.status_code, res.text)

        res.raise_for_status()

def create_rahti(settings: RahtiSettings) -> RahtiProtocol:
    """Factory function for Rahti storage backend."""
    if isinstance(settings, RahtiGithubSettings):
        return RahtiRepo(settings)  # type: ignore
    elif isinstance(settings, RahtiFileSettings):
        return RahtiFile(settings)  # type: ignore
    else:
        raise TypeError(f"Unknown Rahti settings type: {type(settings)!r}")
