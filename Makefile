# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors

.PHONY: dev test check format serve clean

dev:
	uv pip install -e ".[dev]"

test:
	pytest

check:
	ruff check .
	ruff format --check .
	mypy src/

format:
	ruff format .
	ruff check --fix .

serve:
	yttools serve

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
