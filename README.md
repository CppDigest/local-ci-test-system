# Local CI

Run GitHub Actions workflows locally using Docker and [act](https://github.com/nektos/act). Validate CI in about a minute on your machine instead of waiting for remote runners.

## Summary

Local CI parses your workflow file, runs selected jobs in containers with pre-built images, and uses caching (ccache, Boost, CMake, APT) to keep runs fast. It is designed for projects like [Boost.Capy](https://github.com/cppalliance/capy) but works with any GitHub Actions workflow that uses Linux containers.

## Features

- **Workflow analysis** — Inspect jobs and matrix from any `.yml` workflow
- **Local execution** — Run jobs via act with configurable parallelism
- **Caching** — ccache, Boost clone, B2 build dir, CMake config, and APT package cache
- **Pre-built images** — Use or build Docker images per compiler/OS to avoid repeated setup
- **MCP server** — Optional Model Context Protocol interface for AI/IDE integration

## Prerequisites

| Tool      | Purpose              |
|-----------|----------------------|
| Python 3.10+ | CLI runtime        |
| Docker    | Container execution  |
| **[mikefarah/yq](https://github.com/mikefarah/yq) v4+** | YAML parsing -- **not** pip `yq` (PyPI) or [kislyuk/yq](https://github.com/kislyuk/yq) |
| [act](https://github.com/nektos/act)  | Run GitHub Actions locally |

### Host platforms

Local CI is **Linux-container-first**: workflows run Linux containers unless your jobs target Windows runners. On macOS or Windows, Docker Desktop provides the Linux runtime (use the **WSL2** backend on Windows). Setup varies by OS and Docker install; see **[Cross-platform prerequisites](cli/Usage%20Guide.md#cross-platform-prerequisites)** in the User Guide for per-OS installs, Docker caveats, a **preflight checklist**, and notes on **Windows, WSL2, and parallelism**.

### yq (mikefarah/yq v4 or newer)

Several incompatible tools share the name `yq`. Local CI requires **[mikefarah/yq](https://github.com/mikefarah/yq)** **v4+** (`yq --version` output should reference `mikefarah` / `github.com/mikefarah` or report `version v4`). The wrong install fails at runtime with unclear errors. If no suitable binary is on `PATH`, the CLI uses a PyYAML fallback with limited expression support and emits a warning.

| OS | Example install |
|----|-----------------|
| **Windows** | `winget install MikeFarah.yq` or `choco install yq` — or a binary from [Releases](https://github.com/mikefarah/yq/releases) |
| **macOS** | `brew install yq` |
| **Linux** | `sudo snap install yq` **or** install the static binary from [Releases](https://github.com/mikefarah/yq/releases) (verify with `yq --version` before relying on a distro package) |

More options: [mikefarah/yq — Install](https://github.com/mikefarah/yq#install).

## Installation

```bash
cd cli/
pip install .
```

For editable install (development):

```bash
cd cli/
pip install -e ".[dev]"
```

## Validate your installation

A minimal runnable sample lives under [`examples/`](examples/README.md). It runs one Linux job end-to-end (config → workflow → act → results) and documents prerequisite checks, including **mikefarah/yq v4+**:

```bash
cd cli && pip install .
cd ../examples/validation-project
# See examples/README.md for docker tag + localci run steps
```

## Quick Start

```bash
# From your project root (where .github/workflows/ lives)
cd my-project/

# Create config (optional)
localci config init

# See what the workflow does
localci analyze .github/workflows/ci.yml

# List jobs (e.g. Linux only)
localci list --platform linux

# Dry run
localci run --platform linux --dry-run

# Run
localci run --platform linux
```

## Usage

| Command            | Description                    |
|--------------------|--------------------------------|
| `localci analyze <workflow>` | Parse and show jobs/matrix   |
| `localci list`     | List available jobs (optional filters) |
| `localci run`      | Execute selected jobs         |
| `localci status`   | Show run progress             |
| `localci logs <job>` | View job logs               |
| `localci images`   | List/build Docker images      |
| `localci cache`    | Clear or inspect caches       |
| `localci config`   | Show/edit `.localci.yml`      |

Configuration is read from `.localci.yml` in the project root (see [User Guide](cli/Usage%20Guide.md) for full options).

## Project Layout

```text
local-ci-test-system/
├── cli/                 # localci Python package and CLI
│   ├── localci/         # Source
│   └── Usage Guide.md   # Full usage and config reference
├── examples/            # Installation validation sample (see examples/README.md)
├── docs/                # Design and planning
│   ├── Design Guide.md
│   └── Preparation and Plan.md   # Issue breakdown and roadmap
├── images/              # Docker image build scripts (e.g. capy)
└── README.md            # This file
```

## Documentation

- **[User Guide](cli/Usage%20Guide.md)** — Installation, configuration, commands, troubleshooting
- **[Design Guide](docs/Design%20Guide.md)** — Architecture, MCP, caching
- **[Performance and Caching](docs/Performance%20and%20Caching.md)** — Cache layout and config
- **[Preparation and Plan](docs/Preparation%20and%20Plan.md)** — Implementation plan and issue breakdown

## License

BSL-1.0 (see [LICENSE](LICENSE)).
