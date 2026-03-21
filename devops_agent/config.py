"""Configuration loader for the DevOps agent.

Loads projects.toml and Railway API token. Fail-fast on missing config.
"""

import json
import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from .errors import ConfigError

_CONFIG_DIR = Path(__file__).parent
_PROJECTS_FILE = _CONFIG_DIR / "projects.toml"
_RAILWAY_CONFIG = Path.home() / ".railway" / "config.json"


class ProjectConfig(BaseModel):
    """Configuration for a single Railway project."""

    name: str  # TOML key name
    display_name: str = ""
    railway_project_id: str
    production_env_id: str
    staging_env_id: str | None = None
    repo: str
    has_staging: bool = False
    tier: int = 1
    services: list[str] = []
    health_service_id: str = ""
    health_url: str | None = None
    health_timeout: int = 10
    health_expected_status: int = 200
    health_headers: dict[str, str] = {}
    smoke_tests: list[dict] = []


class AgentConfig(BaseModel):
    """Top-level agent configuration."""

    schema_version: int
    railway_token: str
    projects: dict[str, ProjectConfig]


def _load_railway_token() -> str:
    """Load Railway API token. Env var takes precedence over config file."""
    token = os.environ.get("RAILWAY_API_TOKEN")
    if token:
        return token

    if not _RAILWAY_CONFIG.exists():
        raise ConfigError(
            f"Railway token not found. Set RAILWAY_API_TOKEN env var "
            f"or ensure {_RAILWAY_CONFIG} exists."
        )

    try:
        data = json.loads(_RAILWAY_CONFIG.read_text())
        token = data["user"]["apiToken"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ConfigError(f"Failed to read Railway token from {_RAILWAY_CONFIG}: {e}")

    if not token:
        raise ConfigError("Railway apiToken is empty in config file.")
    return token


def _load_projects_toml() -> dict:
    """Load and validate projects.toml."""
    if not _PROJECTS_FILE.exists():
        raise ConfigError(f"Projects config not found: {_PROJECTS_FILE}")

    try:
        data = tomllib.loads(_PROJECTS_FILE.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {_PROJECTS_FILE}: {e}")

    if data.get("schema_version") != 1:
        raise ConfigError(
            f"Unsupported schema_version in {_PROJECTS_FILE}. Expected 1, "
            f"got {data.get('schema_version')}"
        )

    return data


@lru_cache(maxsize=1)
def get_config() -> AgentConfig:
    """Load and cache the full agent configuration. Fail-fast on errors."""
    token = _load_railway_token()
    data = _load_projects_toml()

    projects: dict[str, ProjectConfig] = {}
    for key, value in data.items():
        if key == "schema_version":
            continue
        if not isinstance(value, dict):
            continue
        projects[key] = ProjectConfig(name=key, **value)

    return AgentConfig(
        schema_version=data["schema_version"],
        railway_token=token,
        projects=projects,
    )


def get_project(name: str) -> ProjectConfig:
    """Get a single project config by name. Raises ConfigError if not found."""
    config = get_config()
    if name not in config.projects:
        available = ", ".join(sorted(config.projects.keys()))
        raise ConfigError(f"Project '{name}' not found. Available: {available}")
    return config.projects[name]
