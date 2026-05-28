# Local CI — CLI Package

This directory contains the **localci** Python package: the command-line interface for running GitHub Actions workflows locally with Docker and [act](https://github.com/nektos/act).

## What’s in this directory

- **`localci/`** — Main package: CLI commands, core logic (workflow parsing, job execution, caching, orchestration), and utilities.
- **`tests/`** — Pytest tests for the CLI, config, executor, orchestrator, and workflow handling.
- **`pyproject.toml`** — Package metadata, dependencies, and the `localci` entry point.
- **`Usage Guide.md`** — Full user documentation: installation, all commands, configuration, and troubleshooting.

## Features

The CLI lets you:

- **Analyze** — Parse a workflow file and show jobs, matrix, and structure (`localci analyze`).
- **List** — List available jobs with filters by platform, compiler, or config (`localci list`).
- **Run** — Execute selected jobs locally with optional caching and parallelism (`localci run`).
- **Status & logs** — Check run progress and view job logs (`localci status`, `localci logs`).
- **Images** — List, build, import, and export Docker images used for runs (`localci images`).
- **Cache** — Clear or inspect caches (ccache, Boost, b2-source, CMake, APT) (`localci cache`).
- **Config** — View, create, or edit `.localci.yml` (`localci config`).

Configuration is read from `.localci.yml` in your project root. The tool supports parallel execution, priority-based job ordering, and bind-mounted caches for faster repeated runs.

## Requirements

- **Python 3.10+**
- **Docker** — For running jobs in containers.
- **yq** — For parsing workflow YAML.
- **act** — For executing GitHub Actions locally.

## Installation

From this directory (`cli/`):

```bash
pip install .
```

For development (editable install with dev dependencies):

```bash
pip install -e ".[dev]"
```

Then run:

```bash
localci --help
localci --version
```

## Quick start

```bash
# In your project root (where .github/workflows/ lives)
cd my-project/
localci config init
localci analyze .github/workflows/ci.yml
localci list --platform linux
localci run --platform linux --dry-run
localci run --platform linux
```

## Commands (summary)

| Command | Description |
|--------|-------------|
| `localci analyze <workflow>` | Parse and display workflow structure |
| `localci list` | List jobs (optional filters: platform, compiler, etc.) |
| `localci run` | Execute selected jobs |
| `localci status` | Show run progress |
| `localci logs <job>` | View logs for a job |
| `localci images` | Manage Docker images (list, info, build, clean, import, export) |
| `localci cache` | Clear caches or show stats (clear, stats, update) |
| `localci config` | Show, init, or edit config (show, init, set, get) |

Use `localci <command> --help` for options.

## Project layout

```text
cli/
├── localci/              # Main package
│   ├── cli/              # Command implementations (analyze, list, run, …)
│   ├── core/             # Workflow, executor, orchestrator, cache, queue
│   ├── utils/            # Docker, yq, output, resources
│   └── templates/        # Default config template
├── tests/                # Pytest tests
├── pyproject.toml        # Package definition and entry point
├── Usage Guide.md        # Full usage and config reference
└── README.md             # This file
```

## Documentation

- **[Usage Guide.md](Usage%20Guide.md)** — Complete guide: installation, all commands, configuration, workflows, troubleshooting.
- **[../README.md](../README.md)** — Project overview and quick start.
- **[../docs/](../docs/)** — Design, caching, and implementation docs.

## Development

```bash
# Editable install with dev dependencies
pip install -e ".[dev]"

# Run unit tests (default; integration tests are excluded)
pytest

# Run unit tests with coverage
pytest --cov=localci -m "not integration"

# Integration tests (requires act + Docker)
pytest tests/integration -m integration
```

## License

BSL-1.0 (see [LICENSE](../LICENSE)).
