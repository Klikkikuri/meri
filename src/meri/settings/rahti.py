import logging
from socket import gethostname
from typing import Annotated, Optional, Union

from pydantic import Discriminator, Field, HttpUrl, SecretStr, Tag
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

def _default_committer_email():
    hostname = gethostname()
    name = __package__ or __name__
    return f"{name!s}@{hostname!s}"

class GitHubCommitter(BaseSettings):
    """
    Committer info for bot.

    Based on GitHub `committer` object
    """

    name: str = Field(
        "[🤖 bot] Klikkikuri harbormaster",
        description="The name of the author or committer of the commit."
    )

    email: str = Field(
        default_factory=_default_committer_email,
        description="The email of the author or committer of the commit.",
    )


class RahtiBaseSettings(BaseSettings):
    url: str = Field(
        description="Target URL for Rahti data.",
    )

    model_config = {
        "extra": "ignore"   # Allow extra fields
    }


class RahtiFileSettings(RahtiBaseSettings):
    url: str 

class RahtiGithubSettings(RahtiBaseSettings):
    url: HttpUrl = Field(  # type: ignore
        HttpUrl("https://api.github.com/repos/Klikkikuri/rahti/contents/data.json"),
        description="Target URL for Rahti data.",
    )  
    auth_token: SecretStr = Field(
        description="GitHub token for GitHub API access.",
        alias="GITHUB_TOKEN"
    )

    timeout: Optional[int] = Field(
        30,
        description="Timeout (in seconds) for GitHub API requests.",
    )

    committer: GitHubCommitter

def match_by_url(v: dict) -> str:
    """
    Discriminator function to determine the Rahti settings type based on the URL.
    """
    if isinstance(v, dict) and "url" in v:
        url = v["url"]
        if url.startswith("file://"):
            return "file"
        elif url.startswith("https://api.github.com"):
            return "github"

    logger.error("Unknown Rahti settings type for value: %s", v)
    raise ValueError("Unknown Rahti settings type")

# When adding new Rahti settings types, also update the `match_by_url` function, and `rahti` factory function.
RahtiSettings = Annotated[
    Union[
        Annotated[RahtiFileSettings, Tag("file")],
        Annotated[RahtiGithubSettings, Tag("github")]
    ],
    Discriminator(match_by_url)
]