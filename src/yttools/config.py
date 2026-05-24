# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Application configuration.

Settings load from ``$YTTOOLS_HOME/config.toml`` (default ``~/.yttools``). Hosted
provider API keys fall back to environment variables when the config value is
empty, in this resolution order: config value, then environment variable, then
empty (which leaves the provider disabled).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_HOME = "~/.yttools"

# Hosted-provider API keys are also read from these environment variables.
API_KEY_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


class PathsConfig(BaseModel):
    home: str = DEFAULT_HOME


class FetchConfig(BaseModel):
    # Two parallel downloads by default: enough to be quick, low enough to avoid
    # tripping YouTube's anti-bot rate limiting.
    concurrent_videos: int = 2
    preferred_caption_lang: str = "en"


class YouTubeConfig(BaseModel):
    """Options for talking to YouTube through yt-dlp.

    Cookies clear YouTube's "sign in to confirm you're not a bot" gate. Supply
    either a browser to read cookies from or a path to an exported cookies.txt;
    if both are set, the browser source wins.
    """

    # Browser to read logged-in cookies from: chrome, firefox, safari, brave,
    # edge, chromium, opera, vivaldi. Empty disables it.
    cookies_from_browser: str = ""
    # Path to an exported Netscape-format cookies.txt. Empty disables it.
    cookies_file: str = ""
    # Seconds yt-dlp waits between requests. A small delay reduces the chance of
    # being flagged as a bot. Set to 0 to disable.
    sleep_requests: float = 1.0


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"


class HostedProviderConfig(BaseModel):
    api_key: str = ""
    default_model: str


class LLMConfig(BaseModel):
    default_provider: str = "ollama"
    default_model: str = "llama3.1:8b"
    concurrent_requests: int = 2
    embedding_model: str = "nomic-embed-text"
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: HostedProviderConfig = Field(
        default_factory=lambda: HostedProviderConfig(default_model="claude-sonnet-4-5")
    )
    openai: HostedProviderConfig = Field(
        default_factory=lambda: HostedProviderConfig(default_model="gpt-4o")
    )
    gemini: HostedProviderConfig = Field(
        default_factory=lambda: HostedProviderConfig(default_model="gemini-2.0-flash")
    )


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True


class Settings(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @property
    def home_dir(self) -> Path:
        return Path(self.paths.home).expanduser()

    @property
    def db_path(self) -> Path:
        return self.home_dir / "yttools.db"

    @property
    def config_path(self) -> Path:
        return self.home_dir / "config.toml"

    @property
    def exports_dir(self) -> Path:
        return self.home_dir / "exports"


def resolve_home(home: str | Path | None = None) -> Path:
    """Resolve the data directory, honoring the ``YTTOOLS_HOME`` environment variable."""
    if home is not None:
        return Path(home).expanduser()
    env_home = os.environ.get("YTTOOLS_HOME")
    return Path(env_home).expanduser() if env_home else Path(DEFAULT_HOME).expanduser()


def read_raw_config(home: str | Path | None = None) -> dict[str, Any]:
    """Read the raw config TOML into a dict, or return an empty dict if absent."""
    config_path = resolve_home(home) / "config.toml"
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def _apply_env_key_overrides(settings: Settings) -> None:
    """Fill empty hosted-provider keys from environment variables."""
    for provider, env_var in API_KEY_ENV_VARS.items():
        provider_config: HostedProviderConfig = getattr(settings.llm, provider)
        if not provider_config.api_key:
            env_value = os.environ.get(env_var, "")
            if env_value:
                provider_config.api_key = env_value


def load_settings(home: str | Path | None = None) -> Settings:
    """Load settings from disk and apply environment-variable overrides."""
    resolved_home = resolve_home(home)
    raw = read_raw_config(resolved_home)
    settings = Settings.model_validate(raw)
    settings.paths.home = str(resolved_home)
    _apply_env_key_overrides(settings)
    return settings


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dumps_toml(data: dict[str, Any]) -> str:
    """Serialize a nested config dict to TOML.

    Handles the flat-and-nested-tables shape this project's config uses: top-level
    scalars, single tables, and one level of nested tables.
    """
    lines: list[str] = []
    nested: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            nested.append((key, value))
        else:
            lines.append(f"{key} = {_toml_scalar(value)}")
    for table, table_value in nested:
        sub_tables: list[tuple[str, dict[str, Any]]] = []
        lines.append("")
        lines.append(f"[{table}]")
        for key, value in table_value.items():
            if isinstance(value, dict):
                sub_tables.append((key, value))
            else:
                lines.append(f"{key} = {_toml_scalar(value)}")
        for sub_table, sub_value in sub_tables:
            lines.append("")
            lines.append(f"[{table}.{sub_table}]")
            for key, value in sub_value.items():
                lines.append(f"{key} = {_toml_scalar(value)}")
    return "\n".join(lines).strip() + "\n"


def write_settings(settings: Settings, home: str | Path | None = None) -> Path:
    """Persist settings to ``config.toml``, creating the data directory if needed."""
    resolved_home = resolve_home(home)
    resolved_home.mkdir(parents=True, exist_ok=True)
    config_path = resolved_home / "config.toml"
    payload = settings.model_dump()
    payload["paths"]["home"] = DEFAULT_HOME
    config_path.write_text(dumps_toml(payload), encoding="utf-8")
    return config_path


def get_config_value(key: str, home: str | Path | None = None) -> Any:
    """Read a dotted config key (for example ``llm.default_provider``)."""
    settings = load_settings(home)
    current: Any = settings.model_dump()
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(key)
        current = current[part]
    return current


def set_config_value(key: str, value: str, home: str | Path | None = None) -> Settings:
    """Set a dotted config key and persist. Values are coerced to match the schema."""
    raw = read_raw_config(home)
    parts = key.split(".")
    cursor = raw
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = _coerce_value(value)
    settings = Settings.model_validate(raw)
    write_settings(settings, home)
    return settings


def _coerce_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
