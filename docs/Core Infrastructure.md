# Core Infrastructure — Feature Guide

This document explains Local CI’s **core features** in detail: the CLI, workflow analysis, job execution with act, and Docker image management. These form the foundation that parallel execution and caching build on.

**See also:** [User Guide](../cli/USER_GUIDE.md) for installation and full command reference; [Design Guide](Design%20Guide.md) and [Preparation and Plan](Preparation%20and%20Plan.md) for development and implementation plan.

---

## Overview

Core infrastructure consists of:

1. **CLI** — Commands to analyze workflows, list jobs, run jobs, view status and logs, manage images and cache, and edit config.
2. **Workflow Analyzer** — Parses GitHub Actions YAML to extract jobs, matrix configurations, and requirements (OS, container, compiler, packages).
3. **Job Executor** — Runs a single job locally using [act](https://github.com/nektos/act), with optional bind mounts and environment for caching.
4. **Image Management** — Registry and matching so jobs use pre-built Docker images (or build them when missing), plus CLI to list, build, and clean images.

---

## Feature 1: CLI

### What it does

Provides a single entry point (`localci`) with subcommands for every main operation: inspect workflows, filter and list jobs, run jobs, check status, view logs, manage images and caches, and read or write configuration.

### Commands (summary)

| Command | Purpose |
|--------|---------|
| `localci analyze <workflow>` | Parse workflow and show jobs, matrix, and structure |
| `localci list` | List available jobs (optional filters: platform, compiler, config) |
| `localci run` | Execute selected jobs (with optional caching and parallelism) |
| `localci status` | Show progress of a run or last run |
| `localci logs <job>` | View logs for a job (by index or name) |
| `localci images` | List, build, and manage Docker images |
| `localci cache` | Clear caches or show stats (e.g. ccache) |
| `localci config` | Show, init, or edit `.localci.yml` |

All commands support `--help`. Global options include `--config` to point to a config file and `--workflow` to override the default workflow path.

### Configuration file

Configuration is read from `.localci.yml` in the project root (or from the directory you run in; the tool walks up to find the file). It defines:

- Default workflow file and event
- Parallelism (max jobs, resource limits)
- Platform and job/matrix filters
- Priorities for job ordering
- Cache and image paths
- Logging and execution options (timeout, keep containers, stop on first failure)

**Creating a config:** `localci config init` writes a default `.localci.yml`. Use `localci config show` to inspect the effective config and the User Guide for the full schema.

### Example workflow

```bash
cd my-project/
localci config init
localci analyze .github/workflows/ci.yml
localci list --platform linux
localci run --platform linux --dry-run
localci run --platform linux
localci status
localci logs 5
```

---

## Feature 2: Workflow Analyzer

### What it does

Reads a GitHub Actions workflow file (e.g. `.github/workflows/ci.yml`) and extracts:

- Workflow name and trigger events
- Job list with names, `runs-on`, and `container` (if any)
- **Matrix:** for jobs that use `strategy.matrix.include`, each entry is parsed into a structured “matrix entry” (compiler, version, cxxstd, name, OS, container, asan/ubsan, coverage, extra packages, etc.)
- Steps and dependencies

The result is used by `list` (to show and filter jobs), by `run` (to build the execution queue and pass the right matrix to act), and by the image matcher (to pick or build an image per job).

### How you use it

- **Explicit:** `localci analyze .github/workflows/ci.yml` — prints a human-readable summary (jobs, matrix size, sample entries). Optional: `--format json`, `-o file.json` for machine-readable output.
- **Implicit:** `localci list` and `localci run` call the analyzer internally to get the matrix and apply your filters (platform, compiler, job include/exclude, etc.).

### Output example

Analyze might show something like:

```text
Workflow: CI
File: .github/workflows/ci.yml
Events: push, pull_request

Jobs (2):
  build:
    runs-on: matrix.runs-on
    matrix: 14 configurations
    steps: 12
  changelog:
    runs-on: ubuntu-22.04
    steps: 2

Matrix Configurations (14):
  [1] MSVC 14.42: C++20 (windows-2022)
  [5] GCC 15: C++20 (ubuntu:25.04)
  [8] GCC 13: C++20 coverage (ubuntu-24.04)
  ...
```

The analyzer uses **yq** for YAML parsing. You must have `yq` installed (see User Guide).

---

## Feature 3: Job Executor (act)

### What it does

Executes **one** workflow job locally by invoking [act](https://github.com/nektos/act) with:

- The workflow file (possibly a patched copy with cache mounts and bootstrap skip)
- The target job name and matrix filters (compiler, version, name) so act runs the right matrix entry
- Runner-to-image mapping (e.g. `ubuntu-latest` → `capy-ubuntu-25.04-gcc15:latest`) when using pre-built images
- Optional bind mounts (ccache, Boost, b2-source, CMake, APT) and environment variables (`CCACHE_DIR`, `BOOST_ROOT`, `LOCALCI_B2_SOURCE_DIR`, etc.)
- Per-job act action cache directory to avoid parallel jobs corrupting a shared cache

The executor builds the act command, runs it, captures stdout/stderr, and returns success/failure and duration. It does not decide *which* jobs run or in what order—that is the orchestrator’s job (see [Parallel Execution and Orchestration](Parallel%20Execution%20and%20Orchestration.md)).

### How it works (conceptually)

1. **Input:** A matrix entry (from the workflow analyzer) and an optional image tag (from the image registry/matcher).
2. **Command build:** Act is invoked with `-W <workflow>`, `-j <job>`, `--matrix compiler:gcc --matrix version:15` (etc.), and `-P ubuntu-latest=<image>` when an image is used. Container options (`-v` mounts) are either passed to act or injected into the workflow copy so the job container gets the cache volumes.
3. **Execution:** Act runs the job in a container (or on the host for non-container jobs). The executor waits for completion and collects output and exit code.
4. **Output:** A result object (passed/failed/timeout, duration, log path) used by the orchestrator and by `localci status` / summary.

**Prerequisites:** Docker (for container jobs) and **act** must be installed. The User Guide has installation links.

---

## Feature 4: Docker Image Management

### What it does

- **Registry:** A YAML registry file (e.g. `images/capy/image-registry.yml`; configured via `images.registry` in `.localci.yml`) lists known images: name, file (e.g. `.tar`), Docker tag, OS, compiler(s), optional variants (asan, coverage, x86). The `images.registry` config key accepts a path to a single YAML file; the CLI reads that file to load all registry entries.
- **Matching:** For each job (matrix entry), the system tries to find an image whose OS, compiler, and features match. If found, the job uses that image instead of the workflow’s default container image (e.g. `ubuntu:25.04`), so you avoid re-installing compilers and tools every run.
- **Loading:** Images can be loaded from `.tar` files (e.g. built elsewhere or from CI). The CLI can build images when they are missing if a build script exists (e.g. `images/capy/build-one.sh <name> --save`).
- **CLI:** `localci images list`, `localci images info <name>`, `localci images build` (or build a specific image), and optional cleanup of old images.

### How you use it

- **List images:** `localci images list` — shows registered images and whether they are present locally.
- **Inspect one:** `localci images info capy-ubuntu-25.04-gcc15`.
- **Build:** From the repo root (or a directory where `images/capy` and registry are discoverable), run `localci images build` to build missing images; the tool uses `images/capy/build-one.sh` when available. If an image is missing when you run a job, Local CI can try to build it automatically (see User Guide / Design Guide).
- **Paths:** The images CLI discovers the project root by walking up from the current directory looking for `.localci.yml`, `images/capy`, or `image-registry.yml`, so it works from repo root or from subdirectories like `cli/`.

### Image naming and registry

Images are typically named like `capy-ubuntu-24.04-gcc13` or `capy-ubuntu-25.04-clang20`. The registry maps these to a base OS, compiler set, and optional variants (e.g. asan, coverage, x86). The executor maps the workflow’s `runs-on` (e.g. `ubuntu-latest`) to the chosen image tag so act uses your image instead of pulling the default container.

---

## How the Core Pieces Fit Together

1. **Analyze** reads the workflow and produces a list of matrix entries.
2. **Config and filters** (platform, job, matrix include/exclude) reduce that list to “jobs to run.”
3. **Image matching** assigns an image tag (or none) to each job.
4. **Queue and orchestrator** (see [Parallel Execution and Orchestration](Parallel%20Execution%20and%20Orchestration.md)) decide order and parallelism; for each job they call the **executor** with the matrix entry and image tag.
5. The **executor** builds the act command (with cache mounts and env), runs act, and returns the result.

So: **CLI** → **Analyzer** + **Config** → **Queue** → **Executor** (act + **images** and cache mounts).

---

## Configuration Reference (excerpt)

Relevant parts of `.localci.yml` for core behavior:

```yaml
version: 1
workflow: .github/workflows/ci.yml
event: push

parallel:
  max_jobs: 8
  resource_limit:
    cpu_percent: 80
    memory_percent: 70

platforms:
  linux: true
  windows: false
  macos: false

jobs:
  include: [build]
  exclude: [changelog]

matrix:
  include: []
  exclude: []

priorities: {}   # job name -> priority (lower = higher)

images:
  registry: ~/.localci/images
  auto_build: true

cache:
  enabled: true
  directory: ~/.localci/cache
  # ... ccache, boost, cmake, apt (see Performance and Caching.md)

execution:
  timeout: 3600
  keep_containers: false
  stop_on_first_failure: false
```

Full schema and all options are in the [User Guide](../cli/USER_GUIDE.md).

---

## Reference

- **User Guide:** [cli/USER_GUIDE.md](../cli/USER_GUIDE.md) — Installation, all commands, config schema, troubleshooting.
- **Design Guide:** [Design Guide.md](Design%20Guide.md) — Architecture, MCP, image matching.
- **Preparation and Plan:** [Preparation and Plan.md](Preparation%20and%20Plan.md) — Implementation plan and issue breakdown.
