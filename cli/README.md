# Issue 1: CLI Framework and Command Parser

## Overview

| Field | Value |
|-------|-------|
| **Issue Title** | CLI Framework and Command Parser |
| **Phase** | 1 - Core Infrastructure |
| **Priority** | High |
| **Dependencies** | None |
| **Blocked By** | None |
| **Blocks** | Issues 2, 5, 6, 7, 8 |

## Description

Create an RWX-style CLI with fine-grained commands for local CI operations. The CLI provides intuitive commands for analyzing workflows, listing available jobs, executing tests, and viewing results. This is the foundation component that all other modules build upon.

## Goals

1. **User-Friendly Interface**: Intuitive command structure matching RWX CLI patterns
2. **Fine Granularity**: Support for selecting individual jobs, matrix entries, and configurations
3. **Extensibility**: Plugin architecture for future command additions
4. **Cross-Platform**: Work on Linux, Windows, and macOS
5. **Configuration**: Support for project-specific and user-level configuration

---

## Technical Specification

### Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.11+ | Matches capy CI environment |
| CLI Framework | `click` | Best for complex CLIs with subcommands |
| Configuration | `pydantic` | Type-safe config validation |
| Output Formatting | `rich` | Beautiful terminal output |
| YAML Parsing | `pyyaml` | Standard YAML library |

### Why `click` over alternatives?

| Framework | Pros | Cons |
|-----------|------|------|
| **click** (chosen) | Composable, decorators, groups, plugins | Learning curve |
| `typer` | Type hints, modern | Less flexible for complex CLIs |
| `argparse` | Standard library | Verbose, no subcommand groups |
| `fire` | Auto-generates CLI | Less control |

---

## Command Structure

```
localci <command> [subcommand] [options] [arguments]

COMMANDS:
  analyze     Parse workflow file and display structure
  list        List available jobs and matrix entries
  run         Execute selected jobs locally
  status      Show execution progress
  logs        View logs for a specific job
  images      Manage Docker images
  config      Manage configuration
  version     Show version information
  help        Show help for a command
```

---

## Detailed Command Specifications

### 1. `localci` (Root Command)

The root command group with global options.

```python
@click.group()
@click.option('--config', '-c', type=click.Path(), help='Path to config file')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.option('--quiet', '-q', is_flag=True, help='Suppress non-essential output')
@click.option('--no-color', is_flag=True, help='Disable colored output')
@click.version_option(version='0.1.0', prog_name='localci')
@click.pass_context
def cli(ctx, config, verbose, quiet, no_color):
    """Local CI - Run GitHub Actions workflows locally"""
    ctx.ensure_object(dict)
    ctx.obj['config'] = load_config(config)
    ctx.obj['verbose'] = verbose
    ctx.obj['quiet'] = quiet
    ctx.obj['no_color'] = no_color
```

**Global Options:**

| Option | Short | Type | Description |
|--------|-------|------|-------------|
| `--config` | `-c` | PATH | Custom config file path |
| `--verbose` | `-v` | FLAG | Enable debug logging |
| `--quiet` | `-q` | FLAG | Minimal output |
| `--no-color` | | FLAG | Disable ANSI colors |
| `--version` | | FLAG | Show version |
| `--help` | `-h` | FLAG | Show help |

---

### 2. `localci analyze`

Parse workflow file and display its structure.

```python
@cli.command()
@click.argument('workflow', type=click.Path(exists=True))
@click.option('--event', '-e', default='push', help='Git event type')
@click.option('--format', '-f', type=click.Choice(['table', 'json', 'yaml']), default='table')
@click.option('--output', '-o', type=click.Path(), help='Save output to file')
@click.option('--jobs-only', is_flag=True, help='Show only job names')
@click.option('--matrix-only', is_flag=True, help='Show only matrix entries')
@click.pass_context
def analyze(ctx, workflow, event, format, output, jobs_only, matrix_only):
    """Parse workflow file and display structure."""
    pass
```

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `WORKFLOW` | Yes | Path to workflow YAML file |

**Options:**

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--event` | `-e` | `push` | Git event type (push, pull_request, etc.) |
| `--format` | `-f` | `table` | Output format (table, json, yaml) |
| `--output` | `-o` | stdout | Save to file instead of stdout |
| `--jobs-only` | | false | Show only job names |
| `--matrix-only` | | false | Show only matrix configurations |

**Usage Examples:**

```bash
# Basic usage
localci analyze .github/workflows/ci.yml

# Specify event type
localci analyze .github/workflows/ci.yml --event pull_request

# Output as JSON
localci analyze .github/workflows/ci.yml --format json

# Save to file
localci analyze .github/workflows/ci.yml -f json -o analysis.json

# Show only matrix entries
localci analyze .github/workflows/ci.yml --matrix-only
```

**Output Examples:**

```
$ localci analyze .github/workflows/ci.yml

╭─────────────────────────────────────────────────────────────────╮
│                        Workflow Analysis                         │
╰─────────────────────────────────────────────────────────────────╯

Workflow: CI
File: .github/workflows/ci.yml
Events: push, pull_request

┌─────────────────────────────────────────────────────────────────┐
│ Jobs (2)                                                        │
├─────────────────────────────────────────────────────────────────┤
│ build                                                           │
│   runs-on: ${{ matrix.runs-on }}                                │
│   matrix: 14 configurations                                     │
│   steps: 12                                                     │
│   timeout: 120 minutes                                          │
│                                                                 │
│ changelog                                                       │
│   runs-on: ubuntu-22.04                                         │
│   steps: 2                                                      │
│   timeout: 120 minutes                                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Matrix Configurations (14)                                      │
├────┬─────────────────────────────────┬──────────┬───────────────┤
│ #  │ Name                            │ Platform │ Container     │
├────┼─────────────────────────────────┼──────────┼───────────────┤
│  1 │ MSVC 14.42: C++20               │ windows  │ -             │
│  2 │ MSVC 14.34: C++20 (shared)      │ windows  │ -             │
│  3 │ MinGW: C++20                    │ windows  │ -             │
│  4 │ Apple-Clang (asan+ubsan)        │ macos    │ -             │
│  5 │ GCC 15: C++20                   │ linux    │ ubuntu:25.04  │
│  6 │ GCC 15: C++20 (asan+ubsan)      │ linux    │ ubuntu:25.04  │
│  7 │ GCC 12: C++20                   │ linux    │ ubuntu:22.04  │
│  8 │ GCC 13: C++20 (coverage)        │ linux    │ -             │
│  9 │ Clang 20: C++20-23              │ linux    │ ubuntu:24.04  │
│ 10 │ Clang 20: C++20 (asan+ubsan)    │ linux    │ ubuntu:24.04  │
│ 11 │ Clang 17: C++20                 │ linux    │ -             │
│ 12 │ Clang 20: C++20-23 (x86)        │ linux    │ ubuntu:24.04  │
└────┴─────────────────────────────────┴──────────┴───────────────┘

Platform Summary:
  Linux:   10 configurations
  Windows:  3 configurations
  macOS:    1 configuration (not containerizable)
```

---

### 3. `localci list`

List available jobs with filtering.

```python
@cli.command()
@click.option('--workflow', '-w', type=click.Path(exists=True), help='Workflow file')
@click.option('--platform', '-p', type=click.Choice(['linux', 'windows', 'macos', 'all']), default='all')
@click.option('--compiler', type=str, help='Filter by compiler (gcc, clang, msvc)')
@click.option('--version', type=str, help='Filter by compiler version')
@click.option('--enabled', is_flag=True, help='Show only enabled jobs from config')
@click.option('--disabled', is_flag=True, help='Show only disabled jobs')
@click.option('--format', '-f', type=click.Choice(['table', 'json', 'simple']), default='table')
@click.pass_context
def list(ctx, workflow, platform, compiler, version, enabled, disabled, format):
    """List available jobs and matrix entries."""
    pass
```

**Options:**

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workflow` | `-w` | from config | Path to workflow file |
| `--platform` | `-p` | `all` | Filter by platform |
| `--compiler` | | all | Filter by compiler |
| `--version` | | all | Filter by compiler version |
| `--enabled` | | false | Show only enabled jobs |
| `--disabled` | | false | Show only disabled jobs |
| `--format` | `-f` | `table` | Output format |

**Usage Examples:**

```bash
# List all jobs
localci list

# List only Linux jobs
localci list --platform linux

# List GCC jobs
localci list --compiler gcc

# List Clang 20 jobs
localci list --compiler clang --version 20

# Simple format (for scripting)
localci list --format simple
```

**Output Example:**

```
$ localci list --platform linux

╭─────────────────────────────────────────────────────────────────╮
│                    Available Linux Jobs (10)                     │
╰─────────────────────────────────────────────────────────────────╯

┌────┬─────────────────────────────────┬─────────┬─────────┬────────┐
│ #  │ Name                            │ Compiler│ Image   │ Status │
├────┼─────────────────────────────────┼─────────┼─────────┼────────┤
│  5 │ GCC 15: C++20                   │ gcc-15  │ ✓ ready │ enabled│
│  6 │ GCC 15: C++20 (asan+ubsan)      │ gcc-15  │ ✓ ready │ enabled│
│  7 │ GCC 12: C++20                   │ gcc-12  │ ✓ ready │ enabled│
│  8 │ GCC 13: C++20 (coverage)        │ gcc-13  │ ✗ build │ disabled│
│  9 │ Clang 20: C++20-23              │ clang-20│ ✓ ready │ enabled│
│ 10 │ Clang 20: C++20 (asan+ubsan)    │ clang-20│ ✓ ready │ enabled│
│ 11 │ Clang 17: C++20                 │ clang-17│ ✓ ready │ enabled│
│ 12 │ Clang 20: C++20-23 (x86)        │ clang-20│ ✓ ready │ enabled│
└────┴─────────────────────────────────┴─────────┴─────────┴────────┘

Legend:
  ✓ ready   - Pre-built image available
  ✗ build   - Image will be built on first run
  enabled   - Will run with 'localci run'
  disabled  - Excluded in config
```

---

### 4. `localci run`

Execute jobs locally.

```python
@cli.command()
@click.option('--workflow', '-w', type=click.Path(exists=True), help='Workflow file')
@click.option('--job', '-j', multiple=True, help='Job index or name (can specify multiple)')
@click.option('--platform', '-p', type=click.Choice(['linux', 'windows', 'macos']), help='Run all jobs for platform')
@click.option('--compiler', type=str, help='Filter by compiler')
@click.option('--matrix', '-m', multiple=True, help='Matrix filter (key=value)')
@click.option('--parallel', type=int, default=4, help='Max parallel jobs')
@click.option('--timeout', type=int, default=3600, help='Job timeout in seconds')
@click.option('--dry-run', is_flag=True, help='Preview without executing')
@click.option('--no-cache', is_flag=True, help='Disable build caching')
@click.option('--rebuild-image', is_flag=True, help='Force rebuild Docker image')
@click.option('--keep-containers', is_flag=True, help='Keep containers after execution')
@click.option('--interactive', '-i', is_flag=True, help='Interactive job selection')
@click.pass_context
def run(ctx, workflow, job, platform, compiler, matrix, parallel, timeout, 
        dry_run, no_cache, rebuild_image, keep_containers, interactive):
    """Execute selected jobs locally."""
    pass
```

**Options:**

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workflow` | `-w` | from config | Path to workflow file |
| `--job` | `-j` | all enabled | Job index or name pattern |
| `--platform` | `-p` | from config | Run all jobs for platform |
| `--compiler` | | all | Filter by compiler |
| `--matrix` | `-m` | none | Matrix filter (key=value) |
| `--parallel` | | 4 | Maximum concurrent jobs |
| `--timeout` | | 3600 | Job timeout (seconds) |
| `--dry-run` | | false | Preview without executing |
| `--no-cache` | | false | Disable ccache |
| `--rebuild-image` | | false | Force Docker image rebuild |
| `--keep-containers` | | false | Don't cleanup containers |
| `--interactive` | `-i` | false | Interactive selection |

**Usage Examples:**

```bash
# Run all enabled Linux jobs
localci run --platform linux

# Run specific job by index
localci run --job 5

# Run specific job by name
localci run --job "GCC 15"

# Run multiple specific jobs
localci run --job 5 --job 6 --job 9

# Run with matrix filter
localci run --matrix compiler=gcc --matrix version=15

# Dry run (preview)
localci run --platform linux --dry-run

# Run with higher parallelism
localci run --platform linux --parallel 8

# Interactive mode
localci run --interactive

# Force rebuild image
localci run --job 5 --rebuild-image
```

**Output Example:**

```
$ localci run --platform linux --parallel 4

╭─────────────────────────────────────────────────────────────────╮
│                      Local CI Execution                          │
╰─────────────────────────────────────────────────────────────────╯

Configuration:
  Workflow: .github/workflows/ci.yml
  Platform: linux
  Jobs: 10
  Parallelism: 4
  Cache: enabled

Preparing images...
  ✓ capy-ubuntu-25.04-gcc15 (loaded)
  ✓ capy-ubuntu-22.04-gcc12 (loaded)
  ✓ capy-ubuntu-24.04-clang20 (loaded)
  ✓ capy-ubuntu-24.04-clang17 (loaded)
  ⠋ capy-ubuntu-24.04-gcc13 (building...)

Starting execution...

Progress: 2/10 jobs completed (45s elapsed)

┌─────────────────────────────────────┬──────────┬──────────┐
│ Job                                 │ Status   │ Duration │
├─────────────────────────────────────┼──────────┼──────────┤
│ [5] GCC 15: C++20                   │ ●running │ 32s      │
│ [6] GCC 15: C++20 (asan+ubsan)      │ ●running │ 28s      │
│ [7] GCC 12: C++20                   │ ✓passed  │ 45s      │
│ [9] Clang 20: C++20-23              │ ●running │ 25s      │
│ [10] Clang 20: C++20 (asan+ubsan)   │ ◌pending │ -        │
│ [11] Clang 17: C++20                │ ✓passed  │ 38s      │
│ [12] Clang 20: C++20-23 (x86)       │ ◌pending │ -        │
└─────────────────────────────────────┴──────────┴──────────┘

Press Ctrl+C to cancel, 'l' for logs, 's' for status
```

---

### 5. `localci status`

Show execution progress.

```python
@cli.command()
@click.option('--execution-id', '-e', help='Specific execution ID')
@click.option('--follow', '-f', is_flag=True, help='Follow mode (live updates)')
@click.option('--format', type=click.Choice(['table', 'json']), default='table')
@click.pass_context
def status(ctx, execution_id, follow, format):
    """Show execution progress."""
    pass
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--execution-id` | `-e` | Show specific execution |
| `--follow` | `-f` | Live updates |
| `--format` | | Output format |

**Usage Examples:**

```bash
# Show current/last execution status
localci status

# Follow live updates
localci status --follow

# Show specific execution
localci status --execution-id abc123
```

---

### 6. `localci logs`

View job logs.

```python
@cli.command()
@click.argument('job', required=True)
@click.option('--execution-id', '-e', help='Specific execution ID')
@click.option('--follow', '-f', is_flag=True, help='Follow logs in real-time')
@click.option('--tail', '-n', type=int, default=None, help='Show last N lines')
@click.option('--output', '-o', type=click.Path(), help='Save logs to file')
@click.option('--timestamps', '-t', is_flag=True, help='Show timestamps')
@click.pass_context
def logs(ctx, job, execution_id, follow, tail, output, timestamps):
    """View logs for a specific job."""
    pass
```

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB` | Yes | Job index or name |

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--execution-id` | `-e` | Specific execution |
| `--follow` | `-f` | Follow in real-time |
| `--tail` | `-n` | Last N lines |
| `--output` | `-o` | Save to file |
| `--timestamps` | `-t` | Show timestamps |

**Usage Examples:**

```bash
# View logs for job 5
localci logs 5

# View logs by name
localci logs "GCC 15"

# Follow logs live
localci logs 5 --follow

# Show last 100 lines
localci logs 5 --tail 100

# Save to file
localci logs 5 -o job5.log

# With timestamps
localci logs 5 --timestamps
```

---

### 7. `localci images`

Manage Docker images.

```python
@cli.group()
@click.pass_context
def images(ctx):
    """Manage Docker images."""
    pass

@images.command('list')
@click.option('--format', '-f', type=click.Choice(['table', 'json']), default='table')
@click.pass_context
def images_list(ctx, format):
    """List available images."""
    pass

@images.command('info')
@click.argument('image')
@click.pass_context
def images_info(ctx, image):
    """Show image details."""
    pass

@images.command('build')
@click.option('--all', is_flag=True, help='Build all missing images')
@click.option('--force', is_flag=True, help='Rebuild even if exists')
@click.argument('images', nargs=-1)
@click.pass_context
def images_build(ctx, all, force, images):
    """Build Docker images."""
    pass

@images.command('clean')
@click.option('--older-than', type=str, help='Remove images older than (e.g., 30d)')
@click.option('--unused', is_flag=True, help='Remove unused images')
@click.option('--all', is_flag=True, help='Remove all images')
@click.option('--dry-run', is_flag=True, help='Preview without removing')
@click.pass_context
def images_clean(ctx, older_than, unused, all, dry_run):
    """Clean up Docker images."""
    pass

@images.command('import')
@click.argument('tar_file', type=click.Path(exists=True))
@click.pass_context
def images_import(ctx, tar_file):
    """Import image from tar file."""
    pass

@images.command('export')
@click.argument('image')
@click.option('--output', '-o', type=click.Path(), required=True)
@click.pass_context
def images_export(ctx, image, output):
    """Export image to tar file."""
    pass
```

**Subcommands:**

| Subcommand | Description |
|------------|-------------|
| `list` | List available images |
| `info <image>` | Show image details |
| `build [images...]` | Build images |
| `clean` | Remove old/unused images |
| `import <tar>` | Import from tar file |
| `export <image>` | Export to tar file |

**Usage Examples:**

```bash
# List images
localci images list

# Show image info
localci images info capy-ubuntu-25.04-gcc15

# Build all missing images
localci images build --all

# Build specific image
localci images build capy-ubuntu-25.04-gcc15

# Clean old images
localci images clean --older-than 30d

# Clean unused images
localci images clean --unused

# Import image
localci images import ./my-image.tar

# Export image
localci images export capy-ubuntu-25.04-gcc15 -o image.tar
```

---

### 8. `localci config`

Manage configuration.

```python
@cli.group()
@click.pass_context
def config(ctx):
    """Manage configuration."""
    pass

@config.command('show')
@click.option('--effective', is_flag=True, help='Show effective (merged) config')
@click.pass_context
def config_show(ctx, effective):
    """Show current configuration."""
    pass

@config.command('init')
@click.option('--force', is_flag=True, help='Overwrite existing config')
@click.pass_context
def config_init(ctx, force):
    """Initialize configuration file."""
    pass

@config.command('set')
@click.argument('key')
@click.argument('value')
@click.pass_context
def config_set(ctx, key, value):
    """Set configuration value."""
    pass

@config.command('get')
@click.argument('key')
@click.pass_context
def config_get(ctx, key):
    """Get configuration value."""
    pass
```

**Subcommands:**

| Subcommand | Description |
|------------|-------------|
| `show` | Display current configuration |
| `init` | Create default config file |
| `set <key> <value>` | Set config value |
| `get <key>` | Get config value |

**Usage Examples:**

```bash
# Show config
localci config show

# Show effective (merged) config
localci config show --effective

# Initialize config file
localci config init

# Set value
localci config set parallel.max_jobs 8

# Get value
localci config get parallel.max_jobs
```

---

## Configuration File Schema

### File: `.localci.yml`

```yaml
# .localci.yml - Local CI Configuration
# Place in project root

# Schema version
version: 1

# Default workflow file
workflow: .github/workflows/ci.yml

# Default event type
event: push

# Parallelism settings
parallel:
  max_jobs: 8              # Maximum concurrent jobs
  resource_limit:
    cpu_percent: 80        # Max CPU usage
    memory_percent: 70     # Max memory usage

# Platform configuration
platforms:
  linux: true              # Enable Linux jobs
  windows: false           # Disable Windows (requires Windows host)
  macos: false             # Disable macOS (not containerizable)

# Job filters
jobs:
  # Jobs to include (empty = all)
  include:
    - build
  # Jobs to exclude
  exclude:
    - changelog
    - antora

# Matrix filters
matrix:
  # Include filters (OR logic)
  include:
    - compiler: gcc
    - compiler: clang
  # Exclude filters (AND logic)
  exclude:
    - name: "*coverage*"   # Glob pattern
    - asan: true           # Exclude ASAN builds

# Priority overrides (lower number = higher priority)
priorities:
  "GCC 15: C++20": 1
  "Clang 20: C++20-23": 2
  "GCC 15: C++20 (asan+ubsan)": 10  # Run last

# Docker image settings
images:
  registry: ~/.localci/images    # Image storage location
  auto_build: true               # Build missing images automatically
  cleanup:
    enabled: true
    max_age_days: 30
    max_size_gb: 20

# Cache settings
cache:
  enabled: true
  directory: ~/.localci/cache
  ccache:
    enabled: true
    max_size: 5G
  boost:
    enabled: true
    branch: develop

# Logging
logging:
  level: info                    # debug, info, warning, error
  directory: ~/.localci/logs
  max_files: 10
  max_size_mb: 100

# Execution settings
execution:
  timeout: 3600                  # Default job timeout (seconds)
  keep_containers: false         # Cleanup containers after run
  stop_on_first_failure: false   # Continue on job failure
```

### Pydantic Model

```python
# localci/core/config.py

from pydantic import BaseModel, Field
from typing import Optional
from pathlib import Path

class ParallelConfig(BaseModel):
    max_jobs: int = Field(default=8, ge=1, le=64)
    resource_limit: Optional[dict] = None

class PlatformConfig(BaseModel):
    linux: bool = True
    windows: bool = False
    macos: bool = False

class JobsConfig(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

class MatrixFilter(BaseModel):
    compiler: Optional[str] = None
    version: Optional[str] = None
    name: Optional[str] = None
    asan: Optional[bool] = None
    ubsan: Optional[bool] = None

class MatrixConfig(BaseModel):
    include: list[MatrixFilter] = Field(default_factory=list)
    exclude: list[MatrixFilter] = Field(default_factory=list)

class ImagesConfig(BaseModel):
    registry: Path = Path.home() / ".localci" / "images"
    auto_build: bool = True
    cleanup: Optional[dict] = None

class CacheConfig(BaseModel):
    enabled: bool = True
    directory: Path = Path.home() / ".localci" / "cache"
    ccache: Optional[dict] = None
    boost: Optional[dict] = None

class LoggingConfig(BaseModel):
    level: str = "info"
    directory: Path = Path.home() / ".localci" / "logs"
    max_files: int = 10
    max_size_mb: int = 100

class ExecutionConfig(BaseModel):
    timeout: int = 3600
    keep_containers: bool = False
    stop_on_first_failure: bool = False

class LocalCIConfig(BaseModel):
    version: int = 1
    workflow: Path = Path(".github/workflows/ci.yml")
    event: str = "push"
    parallel: ParallelConfig = Field(default_factory=ParallelConfig)
    platforms: PlatformConfig = Field(default_factory=PlatformConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)
    priorities: dict[str, int] = Field(default_factory=dict)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
```

---

## Project Structure

```
localci/
├── __init__.py                    # Package init, version
├── __main__.py                    # Entry point: python -m localci
├── cli/
│   ├── __init__.py
│   ├── main.py                    # Root CLI group
│   ├── analyze.py                 # analyze command
│   ├── list.py                    # list command
│   ├── run.py                     # run command
│   ├── status.py                  # status command
│   ├── logs.py                    # logs command
│   ├── images.py                  # images command group
│   └── config.py                  # config command group
├── core/
│   ├── __init__.py
│   ├── config.py                  # Configuration models
│   ├── workflow.py                # Workflow analyzer (Issue 2)
│   ├── executor.py                # Job executor (Issue 5)
│   ├── orchestrator.py            # Parallel orchestration (Issue 7)
│   └── registry.py                # Image registry (Issue 3)
├── utils/
│   ├── __init__.py
│   ├── docker.py                  # Docker API wrapper
│   ├── yq.py                      # yq wrapper
│   ├── output.py                  # Rich console helpers
│   └── paths.py                   # Path utilities
├── templates/
│   ├── localci.yml                # Default config template
│   └── dockerfiles/               # Dockerfile templates
└── tests/
    ├── __init__.py
    ├── test_cli.py
    ├── test_config.py
    └── fixtures/
        └── sample_workflow.yml
```

---

## Implementation Plan

### Step 1: Project Setup (Day 1)

```bash
# Create project structure
mkdir -p localci/{cli,core,utils,templates,tests/fixtures}
touch localci/__init__.py localci/__main__.py

# Initialize package
cat > localci/__init__.py << 'EOF'
"""Local CI - Run GitHub Actions workflows locally."""
__version__ = "0.1.0"
EOF

cat > localci/__main__.py << 'EOF'
"""Entry point for python -m localci."""
from localci.cli.main import cli

if __name__ == "__main__":
    cli()
EOF

# Create requirements.txt
cat > requirements.txt << 'EOF'
click>=8.1.0
pyyaml>=6.0
docker>=7.0.0
rich>=13.0.0
pydantic>=2.0.0
EOF

# Create setup.py
cat > setup.py << 'EOF'
from setuptools import setup, find_packages

setup(
    name="localci",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.1.0",
        "pyyaml>=6.0",
        "docker>=7.0.0",
        "rich>=13.0.0",
        "pydantic>=2.0.0",
    ],
    entry_points={
        "console_scripts": [
            "localci=localci.cli.main:cli",
        ],
    },
)
EOF
```

### Step 2: CLI Framework (Day 1-2)

Implement `localci/cli/main.py` with root group and basic commands.

### Step 3: Configuration (Day 2)

Implement `localci/core/config.py` with Pydantic models.

### Step 4: Output Helpers (Day 2)

Implement `localci/utils/output.py` with Rich console helpers.

### Step 5: Individual Commands (Day 3-4)

Implement each command in its own file.

### Step 6: Testing (Day 4-5)

Write unit tests for CLI parsing and configuration.

---

## Dependencies

### Python Packages

```
click>=8.1.0        # CLI framework
pyyaml>=6.0         # YAML parsing
docker>=7.0.0       # Docker API
rich>=13.0.0        # Terminal UI
pydantic>=2.0.0     # Configuration validation
pytest>=7.0.0       # Testing (dev)
pytest-click>=1.1.0 # CLI testing (dev)
```

### System Tools

| Tool | Purpose | Installation |
|------|---------|--------------|
| Docker | Container runtime | Docker Desktop |
| yq | YAML processing | `choco install yq` |
| act | Local Actions | `choco install act-cli` |

---

## Success Criteria

### Functional Requirements

- [ ] All 8 commands implemented
- [ ] Global options work correctly
- [ ] Configuration file loading works
- [ ] Help text for all commands
- [ ] Error messages are clear and actionable
- [ ] Exit codes are correct (0 = success, non-zero = error)

### Non-Functional Requirements

- [ ] Response time < 100ms for simple commands
- [ ] Memory usage < 50MB for CLI parsing
- [ ] Works on Windows, Linux, macOS
- [ ] Python 3.11+ compatible

### Testing Requirements

- [ ] Unit tests for all commands
- [ ] Integration tests with sample workflow
- [ ] Test coverage > 80%

---

## Acceptance Criteria

1. **`localci --help`** shows all commands with descriptions
2. **`localci analyze`** parses capy's ci.yml correctly
3. **`localci list --platform linux`** shows 10 Linux jobs
4. **`localci run --dry-run`** shows execution plan without running
5. **`localci config init`** creates valid .localci.yml
6. **Configuration validation** rejects invalid configs with clear errors
7. **Error handling** provides helpful messages for common issues

---

## References

- [Click Documentation](https://click.palletsprojects.com/)
- [Rich Documentation](https://rich.readthedocs.io/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [RWX CLI](https://docs.rwx.com/cli) - Design inspiration
