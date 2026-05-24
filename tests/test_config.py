# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for configuration loading, persistence, and env-var overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from yttools import config as config_module
from yttools.config import Settings, dumps_toml, load_settings


def test_defaults_match_spec(tmp_home: Path) -> None:
    settings = load_settings()
    assert settings.llm.default_provider == "ollama"
    assert settings.llm.default_model == "llama3.1:8b"
    assert settings.fetch.concurrent_videos == 3
    assert settings.server.port == 8765
    assert settings.server.host == "127.0.0.1"
    assert settings.server.open_browser is True


def test_home_resolution_uses_env(tmp_home: Path) -> None:
    settings = load_settings()
    assert settings.home_dir == tmp_home
    assert settings.db_path == tmp_home / "yttools.db"
    assert settings.config_path == tmp_home / "config.toml"
    assert settings.exports_dir == tmp_home / "exports"


def test_explicit_home_arg_overrides_env(tmp_path: Path) -> None:
    other = tmp_path / "explicit"
    settings = load_settings(home=other)
    assert settings.home_dir == other


def test_toml_file_values_load(tmp_home: Path) -> None:
    (tmp_home / "config.toml").write_text(
        '[llm]\ndefault_provider = "ollama"\ndefault_model = "qwen2.5"\n'
        "[fetch]\nconcurrent_videos = 5\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.llm.default_model == "qwen2.5"
    assert settings.fetch.concurrent_videos == 5


def test_api_key_env_override_fills_empty(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider, env_var = next(iter(config_module.API_KEY_ENV_VARS.items()))
    monkeypatch.setenv(env_var, "sk-from-env")
    settings = load_settings()
    assert getattr(settings.llm, provider).api_key == "sk-from-env"


def test_config_value_wins_over_env(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider, env_var = next(iter(config_module.API_KEY_ENV_VARS.items()))
    default_model = getattr(Settings().llm, provider).default_model
    (tmp_home / "config.toml").write_text(
        f'[llm.{provider}]\napi_key = "sk-from-file"\ndefault_model = "{default_model}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(env_var, "sk-from-env")
    settings = load_settings()
    assert getattr(settings.llm, provider).api_key == "sk-from-file"


def test_set_and_get_roundtrip(tmp_home: Path) -> None:
    config_module.set_config_value("server.port", "9100")
    config_module.set_config_value("llm.default_provider", "ollama")
    assert config_module.get_config_value("server.port") == 9100
    assert (tmp_home / "config.toml").exists()


def test_get_unknown_key_raises(tmp_home: Path) -> None:
    with pytest.raises(KeyError):
        config_module.get_config_value("llm.nonexistent")


def test_set_coerces_bool_and_int(tmp_home: Path) -> None:
    config_module.set_config_value("server.open_browser", "false")
    config_module.set_config_value("fetch.concurrent_videos", "8")
    settings = load_settings()
    assert settings.server.open_browser is False
    assert settings.fetch.concurrent_videos == 8


def test_dumps_toml_roundtrips_through_loader() -> None:
    settings = Settings()
    text = dumps_toml(settings.model_dump())
    assert "[llm.ollama]" in text
    assert 'base_url = "http://localhost:11434"' in text
    assert "concurrent_videos = 3" in text
