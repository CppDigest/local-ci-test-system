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
| [yq](https://github.com/mikefarah/yq) | YAML parsing |
| [act](https://github.com/nektos/act)  | Run GitHub Actions locally |

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

MIT (see [LICENSE](LICENSE) if present).
