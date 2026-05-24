# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""``python -m yttools`` entry point.

Running the module with no arguments starts the web server, matching the
zero-config experience documented in the README. The installed ``yttools``
console script routes through :func:`main` and shows help when run bare.
"""

from __future__ import annotations

import sys

from yttools.cli import app, main


def _run_module() -> None:
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    app()


if __name__ == "__main__":
    _run_module()
else:  # pragma: no cover - re-export for the console-script entry point
    __all__ = ["main"]
