# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `CONTRIBUTING.md` — developer onboarding (setup, tests, lint, pre-commit, PR expectations)
- `CHANGELOG.md` — this file
- `.github/CODEOWNERS` — maintainer routing for reviews

## [0.1.0] - TBD

Pre-release baseline. The CLI package version is `0.1.0` (`cli/pyproject.toml`).

### Added

- `localci` CLI — analyze, list, run, status, logs, images, cache, and config commands
- Configurable workflow patch pipeline for local act execution
- Docker image management and caching (ccache, Boost, CMake, APT)
- CI: ruff lint/format, mypy strict typecheck, unit tests, and act/Docker integration tests
- Runnable validation example under `examples/`
- README yq disambiguation and per-OS install matrix for [mikefarah/yq](https://github.com/mikefarah/yq) v4+

### Pre-release stability (before 1.0)

This project is in **0.x** pre-release. Until **1.0.0**:

- **No stability guarantee** — public CLI flags, config schema (`.localci.yml`), and patch-pipeline behavior may change between minor releases without a major-version bump.
- **Config and workflow patches** — projects depending on specific patch steps or defaults should pin a `localci` version and read the changelog before upgrading.
- **Bug fixes and internal refactors** — may land on `develop` without a deprecation period.
- **1.0 intent** — a stable CLI surface, documented config compatibility policy, and semver guarantees for config and command-line interfaces.

[Unreleased]: https://github.com/cppalliance/local-ci-test-system/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cppalliance/local-ci-test-system/releases/tag/v0.1.0
