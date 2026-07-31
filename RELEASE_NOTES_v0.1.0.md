# localci v0.1.0

<!-- Maintainer: merge the release PR to develop, tag v0.1.0 on that commit, confirm
     CHANGELOG.md at the tag shows ## [0.1.0] - 2026-07-31 (not TBD) and CI is green,
     then publish with: gh release create v0.1.0 --notes-file RELEASE_NOTES_v0.1.0.md -->

First public pre-release of Local CI — run GitHub Actions workflows locally in Docker via [act](https://github.com/nektos/act).

## Highlights

- **`localci` CLI** — analyze, list, run, status, logs, images, cache, and config commands
- **Generic patch profile by default** — stock non-Capy repos run without overrides; `profile: capy` opts into Boost.Capy/B2 workflow patches
- **Docker image management and caching** — ccache, Boost, CMake, and APT layers for fast re-runs
- **Priority-ordered parallel execution** with resource-aware throttling
- **Unsupported-platform honesty** — Windows/macOS matrix jobs fail with an explicit Linux-only message

## Install

```bash
cd cli
pip install -e .
```

See [README.md](README.md) for prerequisites (Docker, act, mikefarah/yq v4+).

## Pre-1.0 stability

This is a **0.x** pre-release. CLI flags, `.localci.yml` schema, and patch-pipeline behavior may change between minor releases. Pin a version and read [CHANGELOG.md](CHANGELOG.md) before upgrading.

Full changelog: https://github.com/cppalliance/local-ci-test-system/blob/v0.1.0/CHANGELOG.md
