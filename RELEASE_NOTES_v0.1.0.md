# localci v0.1.0

<!-- Maintainer: merge the release PR to develop, tag v0.1.0 on that commit, confirm
     CHANGELOG.md at the tag shows ## [0.1.0] - 2026-07-31 (not TBD) and CI is green,
     then publish with: gh release create v0.1.0 --notes-file RELEASE_NOTES_v0.1.0.md -->

First public pre-release of Local CI. Run GitHub Actions workflows locally in Docker via [act](https://github.com/nektos/act).

## Highlights

- `localci` CLI: analyze, list, run, status, logs, images, cache, and config
- Generic patch profile by default; set `profile: capy` for Boost.Capy/B2 workflow patches
- Docker image management and caching (ccache, Boost, CMake, APT)
- Priority-ordered parallel execution with resource limits
- Windows and macOS matrix jobs fail with an explicit Linux-only message instead of running act without an image

## Install

```bash
git clone --branch v0.1.0 https://github.com/cppalliance/local-ci-test-system.git
cd local-ci-test-system/cli
pip install .
```

See [README.md](README.md) for prerequisites (Docker, act, mikefarah/yq v4+).

## Pre-1.0 stability

0.x pre-release: CLI flags, `.localci.yml`, and patch steps may change between minors. Pin a version and read [CHANGELOG.md](CHANGELOG.md) before upgrading.

Full changelog: https://github.com/cppalliance/local-ci-test-system/blob/v0.1.0/CHANGELOG.md
