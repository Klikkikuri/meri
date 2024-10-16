from pathlib import Path
from typing import Literal, Type
from pydantic import Field, RedisDsn
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource
from importlib.metadata import metadata, PackageNotFoundError
from platformdirs import user_config_dir, site_config_dir

try:
    _pkg_name = __name__.split(".")[0]
    _bot_metadata = dict(metadata(_pkg_name))
except (IndexError, PackageNotFoundError):
    _pkg_name = "meri"
    _bot_metadata = dict(metadata(_pkg_name))

_bot_metadata.setdefault("homepage", _bot_metadata.get("repository", ""))

# Locations to look for the settings file
_settings_file_location: list[Path] = [
    Path(user_config_dir("meri")) / "config.yaml",  # User defined settings
    Path(site_config_dir("meri")) / "config.yaml",  # System wide settings
    Path.cwd() / "config.yaml",  # Local settings
    Path("/config/config.yaml")  # Docker settings
]

class Settings(BaseSettings):
    DEBUG: bool = Field(
        False,
        description="Enable debug mode.",
    )

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

    # Logging settings
    LOGGING_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        description="Logging level.",
    )

    redis_dns: RedisDsn = Field(
        "redis://localhost/0",
        description="The Redis connection string.",
    )

    @classmethod
    def settings_customise_sources(cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            YamlConfigSettingsSource(settings_cls),
        )

    model_config = SettingsConfigDict(
        secrets_dir='/run/secrets',
        yaml_file=_settings_file_location,
        yaml_file_encoding="utf-8",
    )

settings = Settings()

del _pkg_name, _bot_metadata, _settings_file_location

if __name__ == "__main__":
    # Test for the settings file
    print(settings.model_dump())