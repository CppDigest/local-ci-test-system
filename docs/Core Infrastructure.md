Title: Local CI Phase 1 - Core Infrastructure (Linux First)

## Overview

Phase 1 establishes the foundation of the Local CI System with four core components:
1. **CLI Framework** - Command-line interface for user interaction
2. **Workflow Analyzer** - Parse GitHub Actions YAML files
3. **Job Executor** - Execute jobs locally using `act`
4. **Linux Base Images** - Pre-built Docker images for capy

**Priority**: Linux support first (10 of 14 capy matrix configurations)

**Target**: Functional local CI execution for single jobs

---

## Issue 1: CLI Framework and Command Parser

### Description

Create an RWX-style CLI with fine-grained commands for local CI operations. The CLI should provide intuitive commands for analyzing workflows, listing available jobs, executing tests, and viewing results.

### Technical Specification

**Language**: Python 3.11+ (matches capy CI environment)

**Framework Options**:
- `click` - Recommended for complex CLIs with subcommands
- `typer` - Modern alternative with type hints
- `argparse` - Standard library option

**Recommended**: `click` for its composability and plugin architecture

### Command Structure

```
localci <command> [options]

Commands:
  analyze     Parse workflow file and display structure
  list        List available jobs and matrix entries
  run         Execute selected jobs locally
  status      Show execution progress
  logs        View logs for a specific job
  images      Manage Docker images
  config      Manage configuration
  version     Show version information
```

### Detailed Command Specifications

#### `localci analyze <workflow.yml>`

Parse and display workflow structure.

```bash
# Basic usage
localci analyze .github/workflows/ci.yml

# Specify event type
localci analyze .github/workflows/ci.yml --event push

# Output as JSON
localci analyze .github/workflows/ci.yml --format json

# Save analysis to file
localci analyze .github/workflows/ci.yml -o analysis.json
```

**Output Example**:
```
Workflow: CI
File: .github/workflows/ci.yml
Events: push, pull_request

Jobs (2):
  build:
    runs-on: matrix.runs-on
    matrix: 14 configurations
    steps: 12
    dependencies: none
    
  changelog:
    runs-on: ubuntu-22.04
    steps: 2
    dependencies: none

Matrix Configurations (14):
  [1] MSVC 14.42: C++20 (windows-2022)
  [2] MSVC 14.34: C++20 shared (windows-2022)
  [3] MinGW: C++20 (windows-2022)
  [4] Apple-Clang (macos-26, asan+ubsan)
  [5] GCC 15: C++20 (ubuntu:25.04)
  [6] GCC 15: C++20 asan+ubsan (ubuntu:25.04)
  [7] GCC 12: C++20 (ubuntu:22.04)
  [8] GCC 13: C++20 coverage (ubuntu-24.04)
  [9] Clang 20: C++20-23 (ubuntu:24.04)
  [10] Clang 20: C++20 asan+ubsan (ubuntu:24.04)
  [11] Clang 17: C++20 (ubuntu-24.04)
  [12] Clang 20: C++20-23 x86 (ubuntu:24.04)
```

#### `localci list [options]`

List available jobs with filtering.

```bash
# List all jobs
localci list

# Filter by platform
localci list --platform linux

# Filter by compiler
localci list --compiler gcc

# Filter by version
localci list --compiler clang --version 20

# Show only enabled jobs (from config)
localci list --enabled
```

#### `localci run [options]`

Execute jobs locally.

```bash
# Run all Linux jobs
localci run --platform linux

# Run specific job by index
localci run --job 5

# Run specific job by name pattern
localci run --name "GCC 15"

# Run with matrix filter
localci run --matrix compiler=gcc --matrix version=15

# Run single matrix entry
localci run --job build --matrix-index 5

# Dry run (preview without executing)
localci run --platform linux --dry-run

# Set parallelism
localci run --platform linux --parallel 4

# Verbose output
localci run --job 5 -v
```

#### `localci status`

Show execution progress.

```bash
# Show current status
localci status

# Follow mode (live updates)
localci status --follow

# Show specific execution
localci status --execution-id abc123
```

**Output Example**:
```
Execution: abc123
Started: 2026-02-06 14:30:00
Duration: 45s

Progress: 3/10 jobs completed

Running (2):
  [5] GCC 15: C++20 .................... 30s
  [6] GCC 15: C++20 asan+ubsan ......... 25s

Completed (3):
  [7] GCC 12: C++20 .................... ✓ 42s
  [8] GCC 13: C++20 coverage ........... ✓ 38s
  [11] Clang 17: C++20 ................. ✓ 35s

Pending (5):
  [9] Clang 20: C++20-23
  [10] Clang 20: C++20 asan+ubsan
  [12] Clang 20: C++20-23 x86
  ...
```

#### `localci logs <job>`

View job logs.

```bash
# View logs for job by index
localci logs 5

# View logs for job by name
localci logs "GCC 15"

# Follow logs in real-time
localci logs 5 --follow

# Show last N lines
localci logs 5 --tail 100

# Save logs to file
localci logs 5 -o job5.log
```

#### `localci images`

Manage Docker images.

```bash
# List available images
localci images list

# Show image details
localci images info capy-ubuntu-25.04-gcc15

# Build missing images
localci images build

# Clean old images
localci images clean --older-than 30d

# Import image
localci images import ./my-image.tar

# Export image
localci images export capy-ubuntu-25.04-gcc15 -o image.tar
```

### Configuration File

**File**: `.localci.yml` in project root

```yaml
# .localci.yml - Local CI configuration for capy

# Default workflow file
workflow: .github/workflows/ci.yml

# Default event type
event: push

# Parallelism settings
parallel:
  max_jobs: 8  # Maximum concurrent jobs
  
# Platform filters (which platforms to run locally)
platforms:
  linux: true
  windows: false  # Requires Windows host
  macos: false    # Not containerizable

# Job filters
jobs:
  include:
    - build
  exclude:
    - changelog  # Skip changelog job locally

# Matrix filters
matrix:
  include:
    - compiler: gcc
    - compiler: clang
  exclude:
    - name: "*coverage*"  # Skip coverage locally

# Priority overrides (lower = higher priority)
priorities:
  "GCC 15: C++20": 1
  "Clang 20: C++20-23": 2

# Image registry location
images:
  registry: ~/.localci/images
  
# Cache settings
cache:
  enabled: true
  directory: ~/.localci/cache
  
# Logging
logging:
  level: info  # debug, info, warning, error
  directory: ~/.localci/logs
```

### Project Structure

```
localci/
├── __init__.py
├── __main__.py           # Entry point
├── cli/
│   ├── __init__.py
│   ├── main.py           # Main CLI group
│   ├── analyze.py        # analyze command
│   ├── list.py           # list command
│   ├── run.py            # run command
│   ├── status.py         # status command
│   ├── logs.py           # logs command
│   └── images.py         # images command
├── core/
│   ├── __init__.py
│   ├── config.py         # Configuration management
│   ├── workflow.py       # Workflow analyzer
│   ├── executor.py       # Job executor
│   └── registry.py       # Image registry
├── utils/
│   ├── __init__.py
│   ├── docker.py         # Docker utilities
│   ├── yq.py             # yq wrapper
│   └── output.py         # Terminal output helpers
└── templates/
    └── localci.yml       # Default config template
```

### Dependencies

```
# requirements.txt
click>=8.1.0
pyyaml>=6.0
docker>=7.0.0
rich>=13.0.0      # Terminal UI
pydantic>=2.0.0   # Configuration validation
```

### Installation

```bash
# Install from source
pip install -e .

# Or install globally
pip install localci

# Verify installation
localci version
```

### Success Criteria

- [ ] All commands implemented and functional
- [ ] Help text for all commands (`localci --help`, `localci run --help`)
- [ ] Configuration file loading and validation
- [ ] Error handling with clear messages
- [ ] Unit tests for CLI parsing

---

## Issue 2: Workflow Analyzer Module

### Description

Parse GitHub Actions YAML workflow files using `yq` to extract jobs, matrix configurations, dependencies, and requirements.

### Technical Specification

**Tool**: `yq` (Go version by mikefarah)

**Installation**:
```bash
# Windows
choco install yq

# Linux
sudo apt-get install yq
# or
sudo snap install yq

# macOS
brew install yq
```

### Workflow Data Model

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class MatrixEntry:
    """Single matrix configuration"""
    index: int
    name: str
    compiler: str
    version: str
    cxxstd: str
    runs_on: str
    container: Optional[str]
    os: str
    architecture: str  # x86_64 or x86
    shared: bool
    build_type: str
    asan: bool
    ubsan: bool
    coverage: bool
    install: list[str]  # Additional packages
    
@dataclass
class Job:
    """Workflow job definition"""
    name: str
    runs_on: str
    container: Optional[str]
    needs: list[str]  # Dependencies
    steps: list[dict]
    matrix: list[MatrixEntry]
    timeout_minutes: int
    
@dataclass
class Workflow:
    """Complete workflow definition"""
    name: str
    file_path: str
    events: list[str]
    jobs: dict[str, Job]
    env: dict[str, str]
```

### yq Query Examples

```bash
# Extract workflow name
yq '.name' .github/workflows/ci.yml

# Extract all job names
yq '.jobs | keys' .github/workflows/ci.yml

# Extract matrix configurations
yq '.jobs.build.strategy.matrix.include' .github/workflows/ci.yml

# Extract specific matrix entry
yq '.jobs.build.strategy.matrix.include[0]' .github/workflows/ci.yml

# Extract job dependencies
yq '.jobs.build.needs' .github/workflows/ci.yml

# Extract container requirements
yq '.jobs.build.strategy.matrix.include[] | .container' .github/workflows/ci.yml

# Extract runs-on for each matrix entry
yq '.jobs.build.strategy.matrix.include[] | .runs-on' .github/workflows/ci.yml

# Filter matrix by compiler
yq '.jobs.build.strategy.matrix.include[] | select(.compiler == "gcc")' .github/workflows/ci.yml

# Extract steps for a job
yq '.jobs.build.steps' .github/workflows/ci.yml

# Extract environment variables
yq '.env' .github/workflows/ci.yml
```

### Python Wrapper

```python
# localci/utils/yq.py

import subprocess
import json
from pathlib import Path

class YqWrapper:
    """Wrapper for yq YAML processor"""
    
    def __init__(self):
        self._check_yq_installed()
    
    def _check_yq_installed(self):
        """Verify yq is available"""
        try:
            result = subprocess.run(
                ['yq', '--version'],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError("yq not found")
        except FileNotFoundError:
            raise RuntimeError("yq not installed. Install with: choco install yq")
    
    def query(self, file: Path, expression: str) -> dict | list | str:
        """Execute yq query and return parsed result"""
        result = subprocess.run(
            ['yq', '-o', 'json', expression, str(file)],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            raise ValueError(f"yq error: {result.stderr}")
        
        if not result.stdout.strip():
            return None
            
        return json.loads(result.stdout)
    
    def extract_jobs(self, file: Path) -> dict:
        """Extract all jobs from workflow"""
        return self.query(file, '.jobs')
    
    def extract_matrix(self, file: Path, job: str) -> list:
        """Extract matrix configurations for a job"""
        return self.query(file, f'.jobs.{job}.strategy.matrix.include')
    
    def extract_events(self, file: Path) -> list:
        """Extract trigger events"""
        on = self.query(file, '.on')
        if isinstance(on, dict):
            return list(on.keys())
        elif isinstance(on, list):
            return on
        else:
            return [on]
```

### Workflow Analyzer Implementation

```python
# localci/core/workflow.py

from pathlib import Path
from .yq import YqWrapper
from .models import Workflow, Job, MatrixEntry

class WorkflowAnalyzer:
    """Analyze GitHub Actions workflow files"""
    
    def __init__(self):
        self.yq = YqWrapper()
    
    def analyze(self, workflow_path: Path, event: str = None) -> Workflow:
        """Parse workflow file and return structured data"""
        
        # Extract basic info
        name = self.yq.query(workflow_path, '.name') or workflow_path.stem
        events = self.yq.extract_events(workflow_path)
        env = self.yq.query(workflow_path, '.env') or {}
        
        # Extract jobs
        jobs_data = self.yq.extract_jobs(workflow_path)
        jobs = {}
        
        for job_name, job_data in jobs_data.items():
            # Extract matrix if present
            matrix = []
            if 'strategy' in job_data and 'matrix' in job_data['strategy']:
                matrix_include = job_data['strategy']['matrix'].get('include', [])
                for i, entry in enumerate(matrix_include):
                    matrix.append(self._parse_matrix_entry(i, entry))
            
            jobs[job_name] = Job(
                name=job_data.get('name', job_name),
                runs_on=job_data.get('runs-on', 'ubuntu-latest'),
                container=job_data.get('container'),
                needs=job_data.get('needs', []),
                steps=job_data.get('steps', []),
                matrix=matrix,
                timeout_minutes=job_data.get('timeout-minutes', 60)
            )
        
        return Workflow(
            name=name,
            file_path=str(workflow_path),
            events=events,
            jobs=jobs,
            env=env
        )
    
    def _parse_matrix_entry(self, index: int, entry: dict) -> MatrixEntry:
        """Parse single matrix entry"""
        return MatrixEntry(
            index=index,
            name=entry.get('name', f'Job {index}'),
            compiler=entry.get('compiler', 'unknown'),
            version=str(entry.get('version', '*')),
            cxxstd=entry.get('cxxstd', '20'),
            runs_on=entry.get('runs-on', 'ubuntu-latest'),
            container=entry.get('container'),
            os=self._extract_os(entry),
            architecture='x86' if entry.get('x86') else 'x86_64',
            shared=entry.get('shared', False),
            build_type=entry.get('build-type', 'Release'),
            asan=entry.get('asan', False),
            ubsan=entry.get('ubsan', False),
            coverage=entry.get('coverage', False),
            install=self._parse_install(entry.get('install', ''))
        )
    
    def _extract_os(self, entry: dict) -> str:
        """Extract OS from container or runs-on"""
        if entry.get('container'):
            return entry['container']
        runs_on = entry.get('runs-on', '')
        if 'ubuntu' in runs_on:
            return runs_on
        elif 'windows' in runs_on:
            return runs_on
        elif 'macos' in runs_on:
            return runs_on
        return 'unknown'
    
    def _parse_install(self, install: str) -> list[str]:
        """Parse install string into package list"""
        if not install:
            return []
        return [pkg.strip() for pkg in install.split()]
    
    def filter_by_platform(self, workflow: Workflow, platform: str) -> list[MatrixEntry]:
        """Filter matrix entries by platform"""
        result = []
        for job in workflow.jobs.values():
            for entry in job.matrix:
                if platform == 'linux' and 'ubuntu' in entry.os.lower():
                    result.append(entry)
                elif platform == 'windows' and 'windows' in entry.os.lower():
                    result.append(entry)
                elif platform == 'macos' and 'macos' in entry.os.lower():
                    result.append(entry)
        return result
```

### Output Format

**JSON Output** (for programmatic use):
```json
{
  "name": "CI",
  "file_path": ".github/workflows/ci.yml",
  "events": ["push", "pull_request"],
  "jobs": {
    "build": {
      "name": "build",
      "runs_on": "matrix.runs-on",
      "matrix": [
        {
          "index": 0,
          "name": "MSVC 14.42: C++20",
          "compiler": "msvc",
          "version": "14.42",
          "runs_on": "windows-2022",
          "container": null,
          "os": "windows-2022",
          "architecture": "x86_64",
          "asan": false,
          "ubsan": false
        }
      ]
    }
  }
}
```

### Success Criteria

- [ ] Parse all capy workflow files correctly
- [ ] Extract all 14 matrix configurations
- [ ] Handle container and runner specifications
- [ ] Filter by platform (linux, windows, macos)
- [ ] Filter by compiler (gcc, clang, msvc)
- [ ] JSON output for programmatic use
- [ ] Unit tests with sample workflows

---

## Issue 5: Job Executor with `act` Integration

### Description

Execute GitHub Actions jobs locally using `act`, with proper container management, output capture, and result aggregation.

### Technical Specification

**Tool**: `act` by nektos

**Installation**:
```bash
# Windows
choco install act-cli

# Linux
curl -s https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash

# macOS
brew install act
```

### Act Command Builder

```python
# localci/core/executor.py

import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass
class ActCommand:
    """Builder for act command"""
    workflow_file: Path
    job: str
    matrix_filters: dict[str, str]
    image: str
    pull: bool = False
    offline: bool = True
    dryrun: bool = False
    verbose: bool = False
    env: dict[str, str] = None
    secrets: dict[str, str] = None
    
    def build(self) -> list[str]:
        """Build act command arguments"""
        cmd = ['act']
        
        # Workflow file
        cmd.extend(['-W', str(self.workflow_file)])
        
        # Target job
        cmd.extend(['-j', self.job])
        
        # Matrix filters
        for key, value in self.matrix_filters.items():
            cmd.extend(['--matrix', f'{key}:{value}'])
        
        # Custom image
        if self.image:
            # Map runner to image
            cmd.extend(['-P', f'ubuntu-latest={self.image}'])
            cmd.extend(['-P', f'ubuntu-24.04={self.image}'])
            cmd.extend(['-P', f'ubuntu-22.04={self.image}'])
        
        # Pull settings
        if not self.pull:
            cmd.append('--pull=false')
        
        # Offline mode
        if self.offline:
            cmd.append('--action-offline-mode')
        
        # Dry run
        if self.dryrun:
            cmd.append('--dryrun')
        
        # Verbose
        if self.verbose:
            cmd.append('-v')
        
        # Environment variables
        if self.env:
            for key, value in self.env.items():
                cmd.extend(['--env', f'{key}={value}'])
        
        # Secrets
        if self.secrets:
            for key, value in self.secrets.items():
                cmd.extend(['--secret', f'{key}={value}'])
        
        return cmd
    
    def __str__(self) -> str:
        return ' '.join(self.build())


class JobExecutor:
    """Execute jobs using act"""
    
    def __init__(self, workflow_file: Path):
        self.workflow_file = workflow_file
        self._check_act_installed()
    
    def _check_act_installed(self):
        """Verify act is available"""
        try:
            result = subprocess.run(
                ['act', '--version'],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError("act not found")
        except FileNotFoundError:
            raise RuntimeError("act not installed. Install with: choco install act-cli")
    
    def execute(
        self,
        job: str,
        matrix_entry: 'MatrixEntry',
        image: str,
        capture_output: bool = True,
        timeout: int = 3600
    ) -> 'ExecutionResult':
        """Execute a single job with specified matrix entry"""
        
        # Build matrix filters from entry
        matrix_filters = {
            'compiler': matrix_entry.compiler,
            'version': matrix_entry.version,
        }
        
        # Build command
        cmd = ActCommand(
            workflow_file=self.workflow_file,
            job=job,
            matrix_filters=matrix_filters,
            image=image,
            pull=False,
            offline=True
        )
        
        # Execute
        start_time = time.time()
        
        if capture_output:
            result = subprocess.run(
                cmd.build(),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            stdout = result.stdout
            stderr = result.stderr
        else:
            result = subprocess.run(
                cmd.build(),
                timeout=timeout
            )
            stdout = None
            stderr = None
        
        duration = time.time() - start_time
        
        return ExecutionResult(
            job=job,
            matrix_entry=matrix_entry,
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
            duration=duration
        )


@dataclass
class ExecutionResult:
    """Result of job execution"""
    job: str
    matrix_entry: 'MatrixEntry'
    success: bool
    exit_code: int
    stdout: Optional[str]
    stderr: Optional[str]
    duration: float
    
    def summary(self) -> str:
        status = "✓" if self.success else "✗"
        return f"[{status}] {self.matrix_entry.name} ({self.duration:.1f}s)"
```

### Container Lifecycle Management

```python
# localci/utils/docker.py

import docker
from docker.models.containers import Container

class DockerManager:
    """Manage Docker containers for job execution"""
    
    def __init__(self):
        self.client = docker.from_env()
    
    def load_image(self, tar_path: Path) -> str:
        """Load image from tar file"""
        with open(tar_path, 'rb') as f:
            images = self.client.images.load(f)
            if images:
                return images[0].tags[0] if images[0].tags else images[0].id
        return None
    
    def save_image(self, image_name: str, tar_path: Path):
        """Save image to tar file"""
        image = self.client.images.get(image_name)
        with open(tar_path, 'wb') as f:
            for chunk in image.save():
                f.write(chunk)
    
    def image_exists(self, image_name: str) -> bool:
        """Check if image exists locally"""
        try:
            self.client.images.get(image_name)
            return True
        except docker.errors.ImageNotFound:
            return False
    
    def list_containers(self, label: str = 'localci') -> list[Container]:
        """List containers with label"""
        return self.client.containers.list(
            all=True,
            filters={'label': label}
        )
    
    def cleanup_containers(self, label: str = 'localci'):
        """Remove all containers with label"""
        for container in self.list_containers(label):
            container.remove(force=True)
    
    def get_resource_usage(self) -> dict:
        """Get current Docker resource usage"""
        return {
            'containers': len(self.client.containers.list()),
            'images': len(self.client.images.list()),
            'disk_usage': self.client.df()
        }
```

### Success Criteria

- [ ] Execute single job with `act`
- [ ] Capture stdout/stderr
- [ ] Handle timeouts
- [ ] Report exit codes
- [ ] Load custom Docker images
- [ ] Cleanup containers after execution
- [ ] Unit tests with mock act execution

---

## Issue 12: Linux Base Images for Capy

### Description

Create pre-built Docker images for all Linux matrix configurations in capy's CI workflow. These images should have all dependencies pre-installed to minimize execution time.

### Image List (8 Images)

| Image Name | Base OS | Compiler | Variants |
|------------|---------|----------|----------|
| `capy-ubuntu-22.04-gcc12` | ubuntu:22.04 | GCC 12 | Standard |
| `capy-ubuntu-24.04-gcc13` | ubuntu:24.04 | GCC 13 | Coverage |
| `capy-ubuntu-24.04-clang17` | ubuntu:24.04 | Clang 17 | Standard |
| `capy-ubuntu-24.04-clang20` | ubuntu:24.04 | Clang 20 | Standard, ASAN |
| `capy-ubuntu-24.04-clang20-x86` | ubuntu:24.04 | Clang 20 | x86 multilib |
| `capy-ubuntu-25.04-gcc15` | ubuntu:25.04 | GCC 15 | Standard, ASAN |
| `capy-ubuntu-25.04-clang20` | ubuntu:25.04 | Clang 20 | Standard |

### Base Dockerfile Template

```dockerfile
# Dockerfile.capy-ubuntu-25.04-gcc15
FROM ubuntu:25.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install base packages
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    cmake \
    ninja-build \
    ccache \
    wget \
    curl \
    ca-certificates \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install GCC 15
RUN apt-get update && apt-get install -y \
    gcc-15 \
    g++-15 \
    && rm -rf /var/lib/apt/lists/*

# Set GCC 15 as default
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-15 100 \
    && update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-15 100

# Install Boost build dependencies
RUN apt-get update && apt-get install -y \
    libssl-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Pre-clone Boost (shallow clone for speed)
RUN git clone --depth=1 --branch=develop \
    https://github.com/boostorg/boost.git /opt/boost \
    && cd /opt/boost \
    && git submodule update --init --depth=1

# Setup ccache
ENV CCACHE_DIR=/cache/ccache
ENV CCACHE_MAXSIZE=2G
RUN mkdir -p /cache/ccache

# Environment variables
ENV CC=gcc-15
ENV CXX=g++-15

# Working directory
WORKDIR /workspace

# Label for identification
LABEL org.localci.project="capy"
LABEL org.localci.os="ubuntu:25.04"
LABEL org.localci.compiler="gcc-15"
```

### ASAN/UBSAN Variant

```dockerfile
# Dockerfile.capy-ubuntu-25.04-gcc15-asan
FROM capy-ubuntu-25.04-gcc15:latest

# ASAN/UBSAN specific flags
ENV ASAN_OPTIONS="detect_leaks=1:detect_stack_use_after_return=1"
ENV UBSAN_OPTIONS="print_stacktrace=1"

# Additional sanitizer packages
RUN apt-get update && apt-get install -y \
    libasan8 \
    libubsan1 \
    && rm -rf /var/lib/apt/lists/*

LABEL org.localci.variant="asan+ubsan"
```

### x86 Multilib Variant

```dockerfile
# Dockerfile.capy-ubuntu-24.04-clang20-x86
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Enable i386 architecture
RUN dpkg --add-architecture i386

# Install base + multilib
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc-multilib \
    g++-multilib \
    git \
    cmake \
    ninja-build \
    ccache \
    && rm -rf /var/lib/apt/lists/*

# Install Clang 20
RUN apt-get update && apt-get install -y \
    clang-20 \
    && rm -rf /var/lib/apt/lists/*

# 32-bit libraries
RUN apt-get update && apt-get install -y \
    libc6-dev-i386 \
    lib32stdc++-13-dev \
    && rm -rf /var/lib/apt/lists/*

ENV CC=clang-20
ENV CXX=clang++-20
ENV CFLAGS="-m32"
ENV CXXFLAGS="-m32"

LABEL org.localci.architecture="x86"
```

### Build Script

```bash
#!/bin/bash
# build-images.sh - Build all capy Docker images

set -e

IMAGES_DIR="./images/capy"
mkdir -p "$IMAGES_DIR"

# Build each image
build_image() {
    local name=$1
    local dockerfile=$2
    
    echo "Building $name..."
    docker build -t "$name:latest" -f "$dockerfile" .
    
    echo "Saving $name.tar..."
    docker save -o "$IMAGES_DIR/$name.tar" "$name:latest"
    
    echo "Done: $name"
}

# Build all images
build_image "capy-ubuntu-22.04-gcc12" "Dockerfile.ubuntu-22.04-gcc12"
build_image "capy-ubuntu-24.04-gcc13" "Dockerfile.ubuntu-24.04-gcc13"
build_image "capy-ubuntu-24.04-clang17" "Dockerfile.ubuntu-24.04-clang17"
build_image "capy-ubuntu-24.04-clang20" "Dockerfile.ubuntu-24.04-clang20"
build_image "capy-ubuntu-24.04-clang20-x86" "Dockerfile.ubuntu-24.04-clang20-x86"
build_image "capy-ubuntu-25.04-gcc15" "Dockerfile.ubuntu-25.04-gcc15"
build_image "capy-ubuntu-25.04-gcc15-asan" "Dockerfile.ubuntu-25.04-gcc15-asan"
build_image "capy-ubuntu-25.04-clang20" "Dockerfile.ubuntu-25.04-clang20"

echo "All images built successfully!"
ls -lh "$IMAGES_DIR"
```

### Image Registry Entry

```yaml
# image-registry.yml
images:
  - name: capy-ubuntu-25.04-gcc15
    file: images/capy/capy-ubuntu-25.04-gcc15.tar
    docker_tag: capy-ubuntu-25.04-gcc15:latest
    os: ubuntu:25.04
    architecture: x86_64
    compilers:
      - gcc-15
    packages:
      - libssl-dev
      - zlib1g-dev
      - cmake
      - ninja-build
      - ccache
    boost_preinstalled: true
    boost_branch: develop
    size_mb: 1500
    created: 2026-02-06T00:00:00Z
    last_used: null
    usage_count: 0
    
  - name: capy-ubuntu-25.04-gcc15-asan
    file: images/capy/capy-ubuntu-25.04-gcc15-asan.tar
    docker_tag: capy-ubuntu-25.04-gcc15-asan:latest
    os: ubuntu:25.04
    architecture: x86_64
    compilers:
      - gcc-15
    variants:
      - asan
      - ubsan
    packages:
      - libssl-dev
      - zlib1g-dev
      - cmake
      - ninja-build
      - ccache
      - libasan8
      - libubsan1
    boost_preinstalled: true
    boost_branch: develop
    size_mb: 1600
    created: 2026-02-06T00:00:00Z
    last_used: null
    usage_count: 0
```

### Success Criteria

- [ ] All 8 Docker images built successfully
- [ ] Images saved as `.tar` files
- [ ] Image registry YAML created
- [ ] Boost pre-cloned in each image
- [ ] ccache configured
- [ ] Images tested with sample `act` execution
- [ ] Total image size < 15GB

---

## Phase 1 Deliverables Summary

| Component | Status | Files |
|-----------|--------|-------|
| CLI Framework | TODO | `localci/cli/*.py` |
| Workflow Analyzer | TODO | `localci/core/workflow.py` |
| Job Executor | TODO | `localci/core/executor.py` |
| Docker Manager | TODO | `localci/utils/docker.py` |
| Linux Images (8) | TODO | `images/capy/*.tar` |
| Image Registry | TODO | `image-registry.yml` |

## Dependencies to Install

```bash
# Python packages
pip install click pyyaml docker rich pydantic

# System tools
choco install yq        # Windows
choco install act-cli   # Windows

# Or on Linux
sudo apt-get install yq
curl -s https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
```

## Next Steps

1. Create GitHub issues for each component
2. Start with Issue 1 (CLI Framework)
3. Build Docker images in parallel
4. Integrate components incrementally
5. Test with capy workflow
