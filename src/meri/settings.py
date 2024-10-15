from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from importlib.metadata import metadata

try:
    _pkg_name = __name__.split(".")[0]
except IndexError:
    _pkg_name = "meri"

_bot_metadata = dict(metadata(_pkg_name))
_bot_metadata.setdefault("homepage", _bot_metadata.get("repository", ""))


class Settings(BaseSettings):
    DEBUG: bool = False

    # Crawler settings
    BOT_ID: str = Field(
        "Klikkikuri",
        description="The name of the bot.",
    )
    BOT_HOMEPAGE: str = Field(
        _bot_metadata["homepage"],
        description="The homepage of the bot.",
    )
    BOT_USER_AGENT: str = Field(
        r"Mozilla/5.0 (compatible; {BOT_ID}/{Version}; +{homepage}",
        description="User agent as f-string template for requests. Can be formatted with "
        "package metadata, and `BOT_ID`.",
    )

    model_config = SettingsConfigDict(secrets_dir='/run/secrets')


settings = Settings()

del _pkg_name, _bot_metadata
