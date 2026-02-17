# Local CI - User Guide

## Table of Contents

- [Introduction](#introduction)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
  - [Creating a Config File](#creating-a-config-file)
  - [Config File Reference](#config-file-reference)
  - [Viewing and Editing Config](#viewing-and-editing-config)
- [Commands](#commands)
  - [Global Options](#global-options)
  - [localci analyze](#localci-analyze)
  - [localci list](#localci-list)
  - [localci run](#localci-run)
  - [localci status](#localci-status)
  - [localci logs](#localci-logs)
  - [localci images](#localci-images)
  - [Building Docker images (images/ scripts)](#building-docker-images-images-scripts)
  - [localci config](#localci-config)
- [Workflows](#workflows)
  - [First-Time Setup](#first-time-setup)
  - [Day-to-Day Usage](#day-to-day-usage)
  - [CI/CD Comparison](#cicd-comparison)
- [Troubleshooting](#troubleshooting)

---

## Introduction

**Local CI** (`localci`) is a command-line tool that runs GitHub Actions
workflows locally using Docker containers and pre-built images. Instead of
waiting 12-15 minutes for remote CI, you can validate your changes in about
a minute on your own machine.

Key features:

- Parse and inspect any GitHub Actions workflow file.
- Select individual jobs or matrix entries to run.
- Manage pre-built Docker images for fast startup.
- Configure job priorities, parallelism, and caching.
- Cross-platform: works on Linux, Windows, and macOS.

---

## Installation

### Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.11+ | Runtime | [python.org](https://www.python.org/downloads/) |
| Docker | Container execution | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| yq | YAML parsing | `choco install yq` / `brew install yq` / `apt install yq` |
| act | Local GitHub Actions | `choco install act-cli` / `brew install act` / `curl -s https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash` |

### Install for usage

Install globally so `localci` is always available on your PATH:

```bash
cd cli/
pip install .
```

### Install for development

If you are working on the localci source code, use a virtual environment
with editable mode so your changes take effect immediately:

```bash
cd cli/
python -m venv venv

# Activate the virtual environment
# Windows (PowerShell):
.\venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# Install in editable mode with dev tools (pytest, coverage)
pip install -e ".[dev]"
```

### Verify installation

```bash
localci --version
# localci, version 0.1.0

localci --help
```

---

## Quick Start

```bash
# 1. Navigate to your project root (where .github/workflows/ lives)
cd my-project/

# 2. Create a configuration file
localci config init

# 3. Inspect a workflow
localci analyze .github/workflows/ci.yml

# 4. List available jobs
localci list --platform linux

# 5. Preview what would run (no execution)
localci run --platform linux --dry-run

# 6. Execute jobs locally
localci run --platform linux
```

---

## Configuration

### Creating a Config File

Generate a default `.localci.yml` in the current directory:

```bash
localci config init
```

If a config file already exists, add `--force` to overwrite it:

```bash
localci config init --force
```

### Config File Reference

The configuration file is called `.localci.yml` and should be placed in your
project root. `localci` automatically discovers it by searching from the
current directory upward (similar to how Git finds `.gitignore`).

Below is a complete reference with default values:

```yaml
# Schema version (always 1 for now)
version: 1

# Default workflow file to operate on
workflow: .github/workflows/ci.yml

# Default Git event type
event: push

# Parallelism settings
parallel:
  max_jobs: 8              # Max concurrent jobs (1-64)
  resource_limit:
    cpu_percent: 80        # Pause new jobs above this CPU usage
    memory_percent: 70     # Pause new jobs above this memory usage

# Which platforms to enable
platforms:
  linux: true
  windows: false           # Requires Windows host with Docker Desktop
  macos: false             # Not containerisable

# Job filters
jobs:
  include: []              # Job names to include (empty = all)
  exclude: []              # Job names to exclude

# Matrix filters (match against matrix entry fields)
matrix:
  include: []              # OR logic between entries
  exclude: []              # Entries to skip

# Priority overrides (lower number = higher priority)
priorities: {}
#   "GCC 15: C++20": 1
#   "Clang 20: C++20-23": 2

# Docker image management
images:
  registry: ~/.localci/images
  auto_build: true         # Build missing images automatically
  cleanup:
    enabled: true
    max_age_days: 30
    max_size_gb: 20

# Build caching (Phase 2)
cache:
  enabled: true
  directory: ~/.localci/cache
  ccache:
    enabled: true
    max_size: 5G
    # dir: ~/.localci/cache/ccache   # optional; default: directory/ccache
  boost:
    enabled: true
    branch: develop
    shallow: true
    # dir: ~/.localci/cache/boost   # optional; default: directory/boost
  cmake:
    enabled: true
    # dir: ~/.localci/cache/cmake   # base dir; per-job: dir/<job_key>

# Logging
logging:
  level: info              # debug, info, warning, error
  directory: ~/.localci/logs
  max_files: 10
  max_size_mb: 100

# Execution behaviour
execution:
  timeout: 3600            # Default job timeout in seconds
  keep_containers: false   # Remove containers after run
  stop_on_first_failure: false  # Stop dispatching new jobs after first failure
```

#### Parallel and orchestration parameters (summary)

| Where | Parameter | Purpose |
|-------|-----------|---------|
| **Config** `parallel` | `max_jobs` | Max concurrent jobs (1–64). |
| **Config** `parallel.resource_limit` | `cpu_percent` | Pause dispatching new jobs when CPU usage exceeds this (default 80). |
| **Config** `parallel.resource_limit` | `memory_percent` | Pause dispatching when memory usage exceeds this (default 70). |
| **Config** `execution` | `timeout` | Per-job timeout in seconds (default 3600). |
| **Config** `execution` | `keep_containers` | If true, do not remove act containers after each run. |
| **Config** `execution` | `stop_on_first_failure` | If true, stop dispatching new jobs after the first job fails. |
| **CLI** `localci run` | `--parallel` | Override `parallel.max_jobs` for this run. |
| **CLI** `localci run` | `--timeout` | Override `execution.timeout` (seconds) for this run. |
| **CLI** `localci run` | `--keep-containers` | Override to keep containers after run (for debugging). |

There is no CLI flag for `stop_on_first_failure`; set it in `.localci.yml` or with `localci config set execution.stop_on_first_failure true`.

#### Key sections

| Section | Purpose |
|---------|---------|
| `parallel` | Control how many jobs run at once and resource limits |
| `platforms` | Enable/disable Linux, Windows, macOS jobs |
| `jobs` | Include or exclude specific job names |
| `matrix` | Filter matrix entries by compiler, version, asan, etc. |
| `priorities` | Override execution order (lower number runs first) |
| `images` | Where Docker images are stored, auto-build, cleanup |
| `cache` | ccache, Boost dependency, and CMake config caching |
| `execution` | Timeouts, container cleanup, failure behaviour |

#### Build caching (Phase 2)

When `cache.enabled` is true, Local CI bind-mounts host cache directories into
containers so that repeated runs reuse build artifacts, the Boost tree, and
CMake configuration.

| Cache | Purpose | Env / path in container |
|-------|---------|--------------------------|
| **ccache** | Compilation cache (B2, CMake builds) | `CCACHE_DIR`, `CCACHE_MAXSIZE` |
| **boost** | Pre-cloned Boost superproject | `BOOST_ROOT`; workflow can skip clone |
| **cmake** | Per-job CMake cache (e.g. `CMakeCache.txt`) | `LOCALCI_CMAKE_CACHE_DIR` |

- **Host cache root:** `cache.directory` (default `~/.localci/cache`). Subdirs
  `ccache/`, `boost/`, `cmake/<job_key>/` are created as needed.
- **Boost bootstrap:** On first run with `cache.boost.enabled`, Local CI runs
  `git clone` (or `git fetch` if the dir already exists) so jobs can use
  `BOOST_ROOT` instead of cloning.
- **CLI:** `--no-cache` disables all build caches for that run. `--cache-dir
  /path` overrides the cache root.

**Cache invalidation:** Caches are not automatically cleared. To force a clean
build, use `--no-cache` for one run, or delete the relevant subdir under
`cache.directory` (e.g. `rm -rf ~/.localci/cache/ccache`). Changing compiler or
toolchain may require clearing ccache or cmake cache.

### Viewing and Editing Config

```bash
# Show the active configuration (from file or defaults)
localci config show

# Show the effective merged configuration
localci config show --effective

# Read a single value (dot-notation)
localci config get parallel.max_jobs

# Update a value
localci config set parallel.max_jobs 16
localci config set execution.timeout 7200
localci config set platforms.windows true
```

You can also point any command at a specific config file:

```bash
localci -c /path/to/.localci.yml config show
```

---

## Commands

### Global Options

These options can be placed before any command:

| Option | Short | Description |
|--------|-------|-------------|
| `--config PATH` | `-c` | Use a specific config file |
| `--verbose` | `-v` | Enable debug-level logging |
| `--quiet` | `-q` | Suppress non-essential output |
| `--no-color` | | Disable coloured terminal output |
| `--version` | | Print version and exit |
| `--help` | `-h` | Show help text |

```bash
# Verbose mode
localci -v run --dry-run

# Quiet mode with no colours (useful for scripting)
localci -q --no-color list --format simple
```

---

### localci analyze

Parse a GitHub Actions workflow file and display its structure.

```
localci analyze WORKFLOW [OPTIONS]
```

| Argument / Option | Description |
|-------------------|-------------|
| `WORKFLOW` | Path to the workflow YAML file (required) |
| `--event`, `-e` | Git event type: `push`, `pull_request`, etc. (default: `push`) |
| `--format`, `-f` | Output format: `table`, `json`, or `yaml` (default: `table`) |
| `--output`, `-o` | Save output to a file instead of stdout |
| `--jobs-only` | Show only job names |
| `--matrix-only` | Show only matrix configurations |

**Examples:**

```bash
# Inspect the CI workflow
localci analyze .github/workflows/ci.yml

# Get JSON output for scripting
localci analyze .github/workflows/ci.yml -f json

# Save analysis to a file
localci analyze .github/workflows/ci.yml -f json -o analysis.json

# Show only matrix entries
localci analyze .github/workflows/ci.yml --matrix-only

# Analyse for pull_request event
localci analyze .github/workflows/ci.yml --event pull_request
```

---

### localci list

List available jobs and matrix entries with filtering.

```
localci list [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--workflow`, `-w` | Workflow file (defaults to config value) |
| `--platform`, `-p` | Filter: `linux`, `windows`, `macos`, or `all` (default: `all`) |
| `--compiler` | Filter by compiler name: `gcc`, `clang`, `msvc` |
| `--version` | Filter by compiler version: `15`, `20`, etc. |
| `--enabled` | Show only jobs enabled in config |
| `--disabled` | Show only jobs disabled in config |
| `--format`, `-f` | Output format: `table`, `json`, `simple` (default: `table`) |

**Examples:**

```bash
# List all jobs
localci list

# List Linux jobs only
localci list --platform linux

# List all GCC jobs
localci list --compiler gcc

# List Clang 20 jobs specifically
localci list --compiler clang --version 20

# Machine-readable output
localci list --format json
```

---

### localci run

Execute selected jobs locally using Docker containers.

```
localci run [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--workflow`, `-w` | Workflow file (defaults to config value) |
| `--job`, `-j` | Job index or name — repeatable for multiple jobs |
| `--platform`, `-p` | Run all jobs for a platform: `linux`, `windows`, `macos` |
| `--compiler` | Filter by compiler |
| `--matrix`, `-m` | Matrix filter as `key=value` — repeatable |
| `--parallel` | Max concurrent jobs (overrides config) |
| `--timeout` | Job timeout in seconds (overrides config) |
| `--dry-run` | Preview the execution plan without running anything |
| `--github-token`, `-t` | GitHub token for downloading external actions |
| `--offline` | Run in offline mode (requires pre-cached actions) |
| `--no-cache` | Disable build caching (ccache, boost, cmake) |
| `--cache-dir` | Override cache root directory |
| `--rebuild-image` | Force Docker image rebuild |
| `--keep-containers` | Don't remove containers after execution |
| `--interactive`, `-i` | Interactively select which jobs to run |
| `--verbose`, `-v` | Show verbose act output |

**Examples:**

```bash
# Run all enabled Linux jobs
localci run --platform linux

# Run a single job by index
localci run --job 5

# Run a single job by name
localci run --job "GCC 15"

# Run multiple specific jobs
localci run --job 5 --job 6 --job 9

# Filter by matrix values
localci run --matrix compiler=gcc --matrix version=15

# Preview without executing
localci run --platform linux --dry-run

# Run with 16 parallel jobs
localci run --platform linux --parallel 16

# Keep containers for debugging
localci run --job 5 --keep-containers

# Force image rebuild
localci run --job 5 --rebuild-image
```

#### GitHub Authentication

If your workflow uses external GitHub Actions (composite actions from other repositories), `act` needs a GitHub token to download them. Without authentication, you'll see errors like:

```
authentication required: Invalid username or token
```

**Solution 1: Environment Variable (Recommended)**

```bash
export GITHUB_TOKEN=ghp_your_token_here
localci run --platform linux
```

**Solution 2: CLI Flag**

```bash
localci run --platform linux --github-token ghp_your_token_here
```

**Solution 3: Offline Mode**

If actions are already cached from a previous run:

```bash
localci run --platform linux --offline
```

**How to Get a GitHub Token:**

1. Go to https://github.com/settings/tokens
2. Click "Generate new token" → "Generate new token (classic)"
3. Select scopes:
   - `repo` (for private repositories)
   - `public_repo` (for public repositories only)
4. Copy the token (starts with `ghp_`)
5. Set it as an environment variable or pass via `--github-token`

**Note**: The token is only used by `act` to download external actions. It's never sent to remote servers or stored permanently.

#### Understanding --dry-run

The `--dry-run` flag is fully functional and prints the execution plan
without touching Docker or running any jobs. Use it to verify your
filters and options before committing to a real run:

```
$ localci run --platform linux --dry-run

ℹ Dry run – execution plan:
  Workflow: .github/workflows/ci.yml
  Platform: linux
  Jobs: all enabled
  Compiler: all
  Matrix filters: none
  Parallelism: 8
  Timeout: 3600s
  Cache: enabled
  Rebuild images: False
  Keep containers: False
```

---

### localci status

Show the progress of a running or completed execution.

```
localci status [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--execution-id`, `-e` | Show a specific execution (default: most recent) |
| `--follow`, `-f` | Live-updating mode |
| `--format` | Output format: `table` or `json` |

**Examples:**

```bash
# Show status of the most recent execution
localci status

# Follow live updates
localci status --follow

# Check a specific execution
localci status --execution-id abc123
```

---

### localci logs

View stdout/stderr logs for a specific job.

```
localci logs JOB [OPTIONS]
```

| Argument / Option | Description |
|-------------------|-------------|
| `JOB` | Job index (e.g. `5`) or name (e.g. `"GCC 15"`) — required |
| `--execution-id`, `-e` | Logs from a specific execution |
| `--follow`, `-f` | Stream logs in real-time |
| `--tail`, `-n` | Show only the last N lines |
| `--output`, `-o` | Save logs to a file |
| `--timestamps`, `-t` | Prefix each line with a timestamp |

**Examples:**

```bash
# View logs for job #5
localci logs 5

# View logs by name
localci logs "GCC 15"

# Follow logs live while the job runs
localci logs 5 --follow

# Show last 50 lines
localci logs 5 --tail 50

# Save to file
localci logs 5 -o build.log

# With timestamps
localci logs 5 --timestamps
```

---

### localci images

Manage Docker images used for local CI execution. This is a command group
with six subcommands.

#### localci images list

```bash
# List all available images
localci images list

# JSON output
localci images list --format json
```

#### localci images info

```bash
# Show details for a specific image
localci images info capy-ubuntu-25.04-gcc15
```

#### localci images build

```bash
# Build all missing images
localci images build --all

# Build a specific image
localci images build capy-ubuntu-25.04-gcc15

# Force rebuild even if image exists
localci images build --force capy-ubuntu-25.04-gcc15
```

#### localci images clean

```bash
# Remove images older than 30 days
localci images clean --older-than 30d

# Remove unused images
localci images clean --unused

# Remove all localci images
localci images clean --all

# Preview what would be removed
localci images clean --unused --dry-run
```

#### localci images import

```bash
# Import an image from a tar file
localci images import ./my-image.tar
```

#### localci images export

```bash
# Export an image to a tar file
localci images export capy-ubuntu-25.04-gcc15 -o image.tar
```

#### Building Docker images (images/ scripts)

You can build the project’s Docker images directly with the scripts under
`images/capy/`. Use this when you are changing Dockerfiles, building without
localci, or exporting images to `.tar` files for transfer.

From the **repository root**:

**Build all images** (in dependency order):

```bash
./images/capy/build-all.sh
```

Optional: export each image to `images/capy/dist/<image-name>.tar`:

```bash
./images/capy/build-all.sh --save
```

**Build a single image** by name:

```bash
./images/capy/build-one.sh capy-ubuntu-24.04-clang20
./images/capy/build-one.sh capy-ubuntu-22.04-gcc12 --save   # also save to .tar
```

Supported image names: `capy-ubuntu-24.04-base`, `capy-ubuntu-25.04-base`,
`capy-ubuntu-22.04-gcc12`, `capy-ubuntu-24.04-gcc13-cov`, `capy-ubuntu-24.04-clang17`,
`capy-ubuntu-24.04-clang20`, `capy-ubuntu-24.04-clang20-asan`, `capy-ubuntu-24.04-clang20-x86`,
`capy-ubuntu-25.04-gcc15`, `capy-ubuntu-25.04-gcc15-asan`. Run
`./images/capy/build-one.sh` with no arguments to print the list.

**Validate an image** (tools, b2, node, compiler):

```bash
./images/capy/test-image.sh capy-ubuntu-24.04-clang20:latest
```

---

### localci config

View, create, and modify the `.localci.yml` configuration file.

#### localci config show

```bash
# Show the active configuration
localci config show

# Show the effective (merged) configuration
localci config show --effective
```

#### localci config init

```bash
# Create a default .localci.yml in the current directory
localci config init

# Overwrite an existing config
localci config init --force
```

#### localci config get

Read a value using dot-notation:

```bash
localci config get parallel.max_jobs       # → 8
localci config get platforms.linux          # → True
localci config get execution.timeout       # → 3600
localci config get cache.ccache.max_size   # → 5G
```

#### localci config set

Write a value using dot-notation:

```bash
localci config set parallel.max_jobs 16
localci config set execution.timeout 7200
localci config set platforms.windows true
localci config set logging.level debug
```

Values are automatically coerced: `true`/`false` become booleans, numeric
strings become integers or floats, and everything else stays a string.

---

## Workflows

### First-Time Setup

1. **Install localci** and its system dependencies (Docker, yq, act).

2. **Create a config file** in your project root:

   ```bash
   cd my-project/
   localci config init
   ```

3. **Edit `.localci.yml`** to match your needs. At minimum, review:
   - `workflow` — path to your CI workflow file
   - `platforms` — enable/disable platforms
   - `jobs.exclude` — skip jobs you don't need locally (e.g. changelog)

4. **Build Docker images** for your matrix configurations:

   ```bash
   localci images build --all
   ```

5. **Run a quick sanity check** with dry-run:

   ```bash
   localci run --platform linux --dry-run
   ```

### Day-to-Day Usage

**Before pushing a commit:**

```bash
# Run all Linux CI jobs locally
localci run --platform linux

# Or run just the jobs you care about
localci run --job "GCC 15" --job "Clang 20"

# Check status while jobs are running
localci status --follow

# Investigate a failure
localci logs "GCC 15"
```

**Quick iteration on a single configuration:**

```bash
# Run one job with verbose output
localci -v run --job 5

# Re-run after fixing the code
localci run --job 5
```

**Trying different compilers:**

```bash
# All GCC jobs
localci run --compiler gcc

# All Clang jobs
localci run --compiler clang

# Specific compiler + version
localci run --matrix compiler=clang --matrix version=20
```

### CI/CD Comparison

| Scenario | GitHub Actions | Local CI |
|----------|---------------|----------|
| Full Linux suite | 12-15 minutes | ~1-2 minutes |
| Incremental build | 12-15 minutes | ~30 seconds |
| Single job | 3-5 minutes | ~15 seconds |
| Offline | Not possible | Fully supported |
| Cost | GitHub Actions minutes | Free (your hardware) |

---

## Troubleshooting

### "No config file found"

`localci` searches for `.localci.yml` starting from the current directory and
walking upward. Make sure you are inside your project directory, or point to
the config file explicitly:

```bash
localci -c /path/to/.localci.yml <command>
```

### "Config file already exists"

Use `--force` to overwrite:

```bash
localci config init --force
```

### "Workflow file not found"

The `analyze` and `run` commands need a valid path to a GitHub Actions
workflow file. Check that the file exists:

```bash
localci analyze .github/workflows/ci.yml
```

If your workflow is in a non-standard location, pass it explicitly with
`--workflow` or set the `workflow` field in `.localci.yml`.

### Command shows "not yet implemented"

Some commands (analyse, list, run, status, logs, images) depend on backend
modules that are being developed in subsequent issues:

| Backend | Required For | Issue |
|---------|-------------|-------|
| Workflow Analyzer | analyze, list | Issue 2 |
| Image Registry | images list/info | Issue 3 |
| Docker Image Management | images build/clean/import/export | Issue 4 |
| Job Executor | run, logs | Issue 5 |
| Orchestrator | run (parallel), status | Issue 7 |

The CLI framework, configuration, and `--dry-run` mode are fully functional.

### Docker not running

Ensure Docker Desktop is running before using `localci run` or
`localci images`. On Windows, verify the WSL2 backend is enabled.

### Verbose mode for debugging

Add `-v` before the command to see debug-level logs:

```bash
localci -v run --job 5
localci -v config show
```

### Piping and scripting

Use `--no-color` and `--quiet` with machine-readable formats for scripts:

```bash
localci -q --no-color list --format json | jq '.jobs[]'
localci -q --no-color config get parallel.max_jobs
```
