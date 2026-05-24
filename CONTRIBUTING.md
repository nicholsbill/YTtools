# Contributing to YTtools

Thank you for your interest in YTtools. This guide covers how to contribute code, documentation, and other improvements to the project.

## Table of contents

- [Code of conduct](#code-of-conduct)
- [Licensing and the CLA](#licensing-and-the-cla)
- [Development setup](#development-setup)
- [Code style](#code-style)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Commit message format](#commit-message-format)
- [Reporting bugs](#reporting-bugs)
- [Requesting features](#requesting-features)

## Code of conduct

This project follows the [Contributor Covenant](./CODE_OF_CONDUCT.md) v2.1. By participating, you agree to abide by its terms.

## Licensing and the CLA

YTtools is dual-licensed:

1. **GNU Affero General Public License v3.0** — the default for all public use. See [LICENSE](./LICENSE).
2. **Commercial License** — available for organizations whose use of YTtools is incompatible with AGPL-3.0 (for example, building proprietary derivatives or hosting modified versions as a service without source disclosure). Contact **wnichols@gmail.com** to inquire.

To make dual-licensing possible, every contributor must sign a Contributor License Agreement (CLA) before their contributions can be merged. The CLA does **not** transfer ownership of your work — you keep your copyright. It grants the project the right to distribute your contributions under both AGPL-3.0 and commercial license terms.

Two CLA forms are available:

- [Individual CLA](./CLA_INDIVIDUAL.md) — for personal contributions, signed by you as an individual.
- [Corporate CLA](./CLA_CORPORATE.md) — for contributions made on behalf of a company.

The first time you open a Pull Request, the CLA Assistant bot will post a comment with a link. Follow the link, review the CLA, authenticate via GitHub, and your signature is recorded. Subsequent PRs are automatic.

If you cannot or do not wish to sign the CLA, your contribution cannot be merged, but you are welcome to maintain a fork under AGPL-3.0.

## Development setup

Requirements:

- Python 3.11 or newer
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip`
- A local install of [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (installed automatically as a dependency)
- Optional: [`ollama`](https://ollama.com) running locally for AI features

Clone and set up:

```bash
git clone https://github.com/nicholsbill/YTtools.git
cd yttools
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Run the development server:

```bash
yttools serve --reload
```

Run the test suite:

```bash
pytest -xvs
```

Run lint and type checks:

```bash
ruff check .
ruff format --check .
mypy src/
```

A `Makefile` provides shortcuts: `make dev`, `make test`, `make check`, `make format`.

## Code style

- **Ruff** is the source of truth for formatting and linting. Configuration is in `pyproject.toml`.
- **mypy** runs in strict mode on `src/yttools/`.
- **Pydantic v2** models throughout — no bare dicts in public function signatures.
- **Async-first** for all I/O. Blocking calls in route handlers are a bug.
- **Docstrings** required for public functions and classes. Google-style format.
- **Type hints** required everywhere except trivial local variables.
- **Comments** only where they add information not obvious from the code itself. Avoid restating what the code does.

## Submitting a Pull Request

1. **Open an issue first** for anything larger than a typo fix or small refactor. Discuss the approach before writing code.
2. **Fork the repository** and create a feature branch off `main`: `git checkout -b feat/your-feature-name`.
3. **Write tests** for new behavior. The CI gate is 70% coverage on `core/`; aim higher on new code.
4. **Run the checks locally** before pushing: `make check` should pass.
5. **Sign the CLA** when prompted on your first PR.
6. **Fill out the PR template** completely. Link the related issue. Include screenshots for UI changes.
7. **Keep PRs focused.** One logical change per PR. Refactors and feature work should be separate PRs.
8. **Respond to review comments** within a reasonable time. Stale PRs may be closed after 30 days of inactivity.

## Commit message format

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <short summary>

<optional body explaining what and why, not how>

<optional footer with breaking change notes or issue references>
```

Common types:

- `feat`: new feature
- `fix`: bug fix
- `docs`: documentation only
- `refactor`: code change that doesn't add features or fix bugs
- `perf`: performance improvement
- `test`: adding or fixing tests
- `build`: build system or dependency changes
- `ci`: CI configuration changes
- `chore`: routine maintenance

Examples:

```
feat(search): add date range filter to FTS queries
fix(fetch): handle members-only videos without crashing
docs(readme): clarify Ollama install steps
```

Breaking changes get a `!` after the type and a `BREAKING CHANGE:` footer:

```
feat(db)!: rename `transcripts.text` to `transcripts.body`

BREAKING CHANGE: schema migration v3 required. Existing databases
will be migrated on first startup.
```

## Reporting bugs

Use the [bug report template](./.github/ISSUE_TEMPLATE/bug_report.md). Include:

- YTtools version (`yttools version`)
- Python version (`python --version`)
- Operating system
- Exact steps to reproduce
- What you expected
- What actually happened
- Any error output or logs (run with `--debug` for more)

## Requesting features

Use the [feature request template](./.github/ISSUE_TEMPLATE/feature_request.md). Frame it around the problem first:

- What are you trying to do?
- Why is it hard or impossible with YTtools today?
- What's the smallest change that would solve it?
- Are there workarounds you're currently using?

The project values small, sharp tools over feature accretion. Not every request will be accepted, but every well-framed one will get a thoughtful response.

## Questions

For general questions, open a Discussion on GitHub rather than an issue. For private inquiries (security reports, commercial licensing, partnership), email **wnichols@gmail.com**.
