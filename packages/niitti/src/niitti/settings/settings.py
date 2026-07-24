import logging
import os
from pathlib import Path
from typing import Type, cast

from dotenv import load_dotenv
from platformdirs import site_config_dir, user_config_dir
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)
from yaml import YAMLError, safe_load

import functools
from importlib.metadata import PackageNotFoundError, metadata

from niitti.settings.const import DEFAULT_APP_AUTHOR, DEFAULT_APP_NAME

load_dotenv()

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=16)
def get_package_metadata(pkg_name: str) -> dict[str, str]:
    """
    Safely retrieve and cache package metadata dictionary for a given package name.
    """
    pkg_meta: dict[str, str] = {}
    base_name = pkg_name.split(".")[0]

    meta_obj = None
    for name in (base_name, pkg_name):
        try:
            meta_obj = metadata(name)
            break
        except (PackageNotFoundError, ValueError):
            continue

    if meta_obj is not None:
        pkg_meta = cast(dict[str, str], dict(meta_obj))  # type: ignore

        if "Home-page" not in pkg_meta and hasattr(meta_obj, "get_all"):
            project_urls = meta_obj.get_all("Project-URL") or []
            for url_entry in project_urls:
                if "," in url_entry:
                    label, url = url_entry.split(",", 1)
                    if label.strip().lower() in ("home-page", "homepage", "home", "repository"):
                        pkg_meta["Home-page"] = url.strip()
                        break

    pkg_meta.setdefault("Version", "0.1.0")
    pkg_meta.setdefault("Home-page", "https://github.com/Klikkikuri")

    return pkg_meta


def lint_yaml_settings_files(paths: list[Path]) -> list[Path]:
    """
    Return existing, valid YAML config files and warn for invalid ones.
    """
    valid_paths: list[Path] = []

    for path in paths:
        if not path.exists() or not path.is_file():
            continue

        try:
            with path.open("r", encoding="utf-8") as f:
                parsed = safe_load(f)
        except (OSError, YAMLError) as e:
            logger.warning("Ignoring invalid YAML settings file '%s': %s", path, e)
            continue

        if parsed is not None and not isinstance(parsed, dict):
            logger.warning(
                "Ignoring YAML settings file '%s': expected a mapping at root, got %s",
                path,
                type(parsed).__name__,
            )
            continue

        valid_paths.append(path)

    return valid_paths


class Settings(BaseSettings):
    """
    Base Settings class for Niitti applications (ABC-like base).
    """

    @classmethod
    def get_package_name(cls) -> str:
        """
        Derive package name dynamically from the subclass module (e.g., 'meri.settings' -> 'meri').
        """
        module = cls.__module__
        if module and module != "__main__":
            return module.split(".")[0]
        return DEFAULT_APP_NAME

    @classmethod
    def get_package_metadata(cls) -> dict[str, str]:
        """
        Get metadata for the current package dynamically.
        """
        pkg_name = cls.get_package_name()
        return get_package_metadata(pkg_name)

    @classmethod
    def get_default_config_locations(cls) -> list[Path]:
        """
        Get standard locations to search for settings files, derived dynamically from package name.
        Can be overridden by subclasses to provide custom configuration file search paths.
        """
        pkg_name = cls.get_package_name()
        user_cfg = Path(user_config_dir(pkg_name, DEFAULT_APP_AUTHOR), "config.yaml")
        site_cfg = Path(site_config_dir(pkg_name, DEFAULT_APP_AUTHOR), "config.yaml")
        locations: list[Path] = [
            Path("/app/config.yaml"),
            Path("/app/instance/config.yaml"),
            Path("/config/config.yaml"),
            Path.cwd() / "config.yaml",
            site_cfg,
            user_cfg,
        ]
        if conf_file := os.getenv("KLIKKIKURI_CONFIG_FILE"):
            locations.insert(0, Path(conf_file))

        return lint_yaml_settings_files(locations)

    model_config = SettingsConfigDict(
        secrets_dir="/run/secrets" if Path("/run/secrets").exists() else None,
        yaml_file_encoding="utf-8",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        yaml_locations = cls.get_default_config_locations()
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_locations),
        )
