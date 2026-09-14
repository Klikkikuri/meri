from socket import gethostname

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


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


class RahtiSettings(BaseSettings):
    """
    Where Rahti data is pulled from and pushed to, and the credentials for it.

    The storage driver is chosen from the location when Rahti is used (see :mod:`meri.rahti.storage`), so one
    settings model serves every driver, and the manifest and rule reads use the same credentials.
    """

    url: str | None = Field(
        None,
        description="Location of the Rahti `data.json`: a path, a `file://` URL, or a GitHub Contents API or raw "
        "URL. If empty, `file:///app/instance/rahti/data.json`. The manifest never sets it.",
    )
    auth_token: SecretStr | None = Field(
        default=None,
        description="Token for the storage host, such as a GitHub token.",
        alias="GITHUB_TOKEN",
    )

    timeout: int = Field(
        30,
        description="Timeout (in seconds) for requests to the storage host.",
    )

    committer: GitHubCommitter = Field(
        default_factory=lambda: GitHubCommitter(),  # type: ignore
        description="Committer info for bot.",
    )

    model_config = {
        "extra": "ignore"   # Allow extra fields
    }

    @field_validator("url")
    @classmethod
    def _empty_url(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None
