# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from yttools.config import API_KEY_ENV_VARS
from yttools.core.db import Database

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated YTTOOLS_HOME directory for a single test."""
    home = tmp_path / "ythome"
    home.mkdir()
    monkeypatch.setenv("YTTOOLS_HOME", str(home))
    return home


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """A migrated, file-backed database in a temporary directory."""
    database = Database.open(tmp_path / "test.db")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(autouse=True)
def _clear_api_key_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep ambient API-key environment variables out of tests."""
    for env_var in API_KEY_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)
    yield
