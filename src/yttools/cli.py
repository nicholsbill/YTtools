# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Typer command-line interface.

Every web UI action has a CLI equivalent so the tool can be scripted. Commands are
added per release; v0.1.0 ships ``fetch``, ``search``, ``list``, ``serve``,
``config``, ``db``, and ``version``.
"""

from __future__ import annotations

import typer

from yttools import config as config_module
from yttools.version import __version__

app = typer.Typer(
    name="yttools",
    help="Local-first toolkit for searching public YouTube transcripts.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(help="Read and write configuration values.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Dotted key, e.g. llm.default_provider"),
) -> None:
    """Print a configuration value."""
    try:
        value = config_module.get_config_value(key)
    except KeyError:
        typer.echo(f"Unknown config key: {key}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(str(value))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted key, e.g. llm.default_provider"),
    value: str = typer.Argument(..., help="New value"),
) -> None:
    """Set a configuration value and persist it to config.toml."""
    config_module.set_config_value(key, value)
    typer.echo(f"Set {key} = {value}")


def main() -> None:
    """Console-script entry point."""
    app()
