# Contributing to Local CI

Thank you for your interest in contributing. This document is the onboarding entry point for development setup, testing, and pull requests.

## Prerequisites

Before you start, install the runtime tools listed in the [README](README.md#prerequisites):

| Tool | Notes |
|------|-------|
| Python 3.10+ | CLI runtime |
| Docker | Container execution (Linux containers) |
| [act](https://github.com/nektos/act) | Run GitHub Actions locally |
| **[mikefarah/yq v4+](https://github.com/mikefarah/yq)** | **Required** — see below |

### yq (read this first)

Several incompatible tools share the name `yq`. Local CI requires **[mikefarah/yq v4+](https://github.com/mikefarah/yq)** — **not** the PyPI `yq` package and **not** [kislyuk/yq](https://github.com/kislyuk/yq). Installing the wrong tool causes unclear runtime failures.

**→ Full disambiguation table and per-OS install commands: [README — yq (mikefarah/yq v4 or newer)](README.md#yq-mikefarahyq-v4-or-newer)**

Verify your install:

```bash
yq --version
# Should reference mikefarah / github.com/mikefarah or report version v4+
```

If no suitable binary is on `PATH`, the CLI falls back to PyYAML with limited expression support and emits a warning (`localci.utils.yq`).

## Development setup

All Python work happens under `cli/`:

```bash
cd cli
pip install -e ".[dev]"
```

The `[dev]` extra installs pytest, ruff, mypy, pre-commit, and type stubs (`cli/pyproject.toml`).

Optional — install git hooks so lint and typecheck run before each commit:

```bash
pre-commit install
```

Hooks are defined in [`.pre-commit-config.yaml`](.pre-commit-config.yaml) and mirror the CI lint and typecheck jobs.

## Running tests

From `cli/`:

```bash
pytest
```

This runs unit tests under `tests/` and **excludes** `tests/integration/` by default (`norecursedirs` in `pyproject.toml`). CI runs the same default set with coverage:

```bash
pytest --cov=localci --cov-report=term-missing -m "not integration"
```

### Integration tests (opt-in)

Integration tests exercise real `act` and Docker subprocesses. They require both tools installed and a running Docker daemon. When unavailable, the suite skips automatically.

To run them locally:

```bash
pytest tests/integration -m integration -v
```

CI runs integration tests in a separate job after lint, typecheck, and unit tests pass (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Linting and formatting

From `cli/`:

```bash
ruff check localci/ tests/
ruff format localci/ tests/
```

Ruff configuration lives in [`cli/pyproject.toml`](cli/pyproject.toml) (`[tool.ruff]`).

## Type checking

From `cli/`:

```bash
mypy localci/
```

Mypy runs in `strict` mode. Legacy CLI modules are temporarily suppressed via per-module overrides; `localci.core.*` and `localci.utils.*` must pass strict typing. Do not add new modules to the `ignore_errors` list.

## Pre-commit

Run all hooks against the full tree (useful before pushing):

```bash
pre-commit run -a
```

## Pull requests

1. Branch from `develop` (or `main` for release fixes).
2. Keep changes focused; link related issues in the PR description.
3. Ensure CI is green — lint, typecheck, unit tests, and integration tests must pass.
4. Request review from at least one maintainer. PRs need **at least one approving review** before merge.

## Maintainers

Current maintainer: [@bradjin8](https://github.com/bradjin8) (see [`.github/CODEOWNERS`](.github/CODEOWNERS)).

For usage questions and configuration, see the [User Guide](cli/Usage%20Guide.md).
