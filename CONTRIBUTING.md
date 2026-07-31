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

Local CI requires **[mikefarah/yq v4+](https://github.com/mikefarah/yq)**. Disambiguation, per-OS install commands, and verification: [README — yq (mikefarah/yq v4 or newer)](README.md#yq-mikefarahyq-v4-or-newer).

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

1. Branch from `develop`.
2. Keep changes focused; link related issues in the PR description.
3. Ensure CI is green: lint, typecheck, unit tests, and integration tests must pass.
4. Request review from at least one maintainer. PRs need **at least one approving review** before merge.
5. Update `CHANGELOG.md` for user-visible changes under `## [Unreleased]`.

## Branching and releases

- `develop` is the default branch where day-to-day work lands and releases are cut from.
- To cut release `X.Y.Z` when `cli/pyproject.toml` already declares that version:
  1. Move the `## [Unreleased]` entries in [CHANGELOG.md](CHANGELOG.md) under a new
     `## [X.Y.Z] - YYYY-MM-DD` heading, leave `## [Unreleased]` empty, and update
     the link references at the bottom.
  2. Create `RELEASE_NOTES_vX.Y.Z.md` from the new `## [X.Y.Z]` section (see
     [RELEASE_NOTES_v0.1.0.md](RELEASE_NOTES_v0.1.0.md) for the format).
  3. Merge the changelog and release-notes update to `develop` via PR; wait for CI
     to be green.
  4. Create an annotated tag `vX.Y.Z` on the merged `develop` commit and push it
     (`git tag -a vX.Y.Z` then `git push origin vX.Y.Z`). If `vX.Y.Z` already
     exists, stop and verify `git rev-parse vX.Y.Z^{commit}` matches the merged
     release commit (`git rev-parse origin/develop`); do not publish from a tag
     that points elsewhere.
  5. Validate the tag: confirm `git show vX.Y.Z:CHANGELOG.md` has
     `## [X.Y.Z] - YYYY-MM-DD` (not `TBD`) and CI is green on the tagged commit.
  6. Publish the GitHub Release
     (`gh release create vX.Y.Z --notes-file RELEASE_NOTES_vX.Y.Z.md`) only after
     steps 4–5 confirm the tag points to the merged release commit.
  7. Confirm the `[Unreleased]` and `[X.Y.Z]` compare links at the bottom of
     [CHANGELOG.md](CHANGELOG.md) resolve after the new tag is created.

## Maintainers

Current maintainer: [@bradjin8](https://github.com/bradjin8) (see [`.github/CODEOWNERS`](.github/CODEOWNERS)).

For usage questions and configuration, see the [User Guide](cli/Usage%20Guide.md).
