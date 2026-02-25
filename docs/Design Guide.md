# Local CI Test System

## Overview

A system to execute GitHub Actions CI workflows locally with pre-built, optimized OS images to minimize test time. The system analyzes CI workflows, prepares test environments, and executes tests efficiently.

---

## Architecture Components

### CI Workflow Analyzer
- Uses `yq` for parsing GitHub Actions YAML files
- Extracts jobs, matrix configurations, steps, dependencies
- Identifies OS/container requirements, compiler versions, packages

### Image Management System
- Maintains registry of pre-built Docker images (stored as `.tar` files)
- Implements image matching algorithm to select optimal images
- Creates new images on-demand during job execution when no matching image exists
- Saves newly created images for future reuse
- Removes old images to manage disk space

### Test Orchestrator
- Coordinates parallel test execution (~20 jobs simultaneously)
- Uses Docker API to manage containers
- Provides real-time progress monitoring
- Aggregates test results

### MCP Server Interface
- Exposes endpoints for triggering tests
- Uses `yq` for workflow analysis
- Uses `act` for workflow execution
- Supports async operations for long-running tests

---

## Execution Workflow

All steps are performed via scripts within the MCP server.

### Step 1: Extract Workflow Information

Extract jobs, configuration matrix, and dependencies from workflow files based on input Git event name using `yq`:

- Parse workflow files (`.github/workflows/*.yml`)
- Extract jobs matching Git event (`push`, `pull_request`, ...)
- Extract matrix configurations for each job
- Extract job dependencies
- Extract OS/container requirements, compiler versions, packages

**Output**: Structured data with all jobs, matrix entries, and dependencies.

### Step 2: Determine Test List

Determine which jobs and matrix entries to execute from configuration file:

- Read configuration file
- Apply filters: which jobs to run, which matrix entries to include/exclude
- Extract or assign job priorities (higher priority = execute first)
- Respect job dependencies to determine execution order
- Create ordered list of (job, matrix_entry, priority) tuples, sorted by priority (highest first)

**Priority Rules:**
- Jobs with higher priority must complete before lower priority jobs can start
- Within same priority level, jobs can run in parallel (up to parallel limit)
- Priority can be extracted from workflow file or assigned via configuration

**Output**: Ordered list of (job_name, matrix_entry, priority) tuples to execute, sorted by priority.

### Step 3: Analyze Image Requirements

For each (job, matrix_entry) pair in the test list, determine required Docker image:

**3.1. Identify Required Docker Image Type**
- Container image (from `matrix.container` or `job.container.image`)
- Runner OS (from `matrix.runs-on` or `job.runs-on`)
- Additional requirements (compiler versions, packages, architecture)

**3.2. Match with Image Registry**
- Use two-mark Image Matching Algorithm to evaluate all available images
- Calculate essential marks (OS, compiler) and extra marks (packages, tools)
- Record execution plan:
  - **If any image has essential marks = 100**: Use a highest extra marks image / Record (job, matrix_entry) → (needs_build = "false", best_image_path)
  - **If no image has essential marks = 100**: Use a highest essential marks image / Record (job, matrix_entry) → (needs_build = "true", best_base_image_path)

**Output**: Execution plan mapping each (job, matrix_entry) to image info including score and requirements.

### Step 4: Execute Jobs with Parallel Control

Execute jobs with parallel control and priority-based resource management. Jobs are processed from the queue in priority order (highest first), with multiple jobs running concurrently up to the parallelization limit. Lower priority jobs cannot start until all higher priority jobs have completed.

For each job, prepare image (load or build) and execute `act`. If an image doesn't exist, it is built synchronously before the job runs - jobs are never skipped due to missing images.

**4.1. Parallel Execution Manager**
- Set maximum concurrent jobs (e.g., ~20 parallel)
- Monitor resource usage (CPU, memory, disk)
- Maintain priority-ordered queue of pending (job, matrix_entry, priority) tuples
- Track running jobs (active `act` processes/containers) with their priorities
- Track highest priority of running jobs to enforce priority constraints

**4.2. Per-Job Execution Flow**

For each job ready to execute (when under parallel limit AND priority allows):

**Priority Check:**
- Job can only start if:
  1. Under parallel limit (e.g., < 20 running jobs)
  2. No higher priority jobs are running (all higher priority jobs completed)
  3. All job dependencies (if any) are satisfied

1. **Image Preparation:**
   - Check execution plan from Step 3 for image selection
   
   - **If image has full essential marks (= 100)**:
     - Load matched image
     - Tag appropriately for `act`
   
   - **If no image has full essential marks**:
     - Load base image (highest essential marks)
     - Create new image from this base:
       - Upgrade/install to meet essential requirements (OS version, architecture, compiler)
       - Install all required packages and tools
       - Save the new image for future reuse
       - Update image registry/index with new image metadata
     - Tag appropriately for `act`
   
   - Image loading/creation is synchronous - job waits for image to be ready before proceeding

2. **Execute `act`:**
   - Execute `act` command
   - Capture stdout/stderr for logs
   - Monitor process/container status via Docker API

3. **Cleanup On Completion:**
   - Extract exit code, parse results, update job status
   - Clean up `act` containers
   - Unload Docker image to free memory

**4.3. Job Completion Handling**
- When one job completes:
  - Remove from running jobs list
  - Add results to completed jobs
  - Update highest running priority (if this was the last job of that priority)
  - Check queue for next pending job (in priority order)
  - Start next job if:
    - Under parallel limit
    - No higher priority jobs are running
    - All dependencies satisfied
  - Update progress tracking

**4.4. Progress Tracking**
- Track overall progress: `X/Y jobs completed`
- Track per-job status: `pending`, `running`, `completed`, `failed`
- Provide real-time updates via MCP interface

**Output**: Complete execution results for all jobs in test list.

---

## Appendix

### A. Tools

#### `yq`
- **Purpose**: Parse and analyze GitHub Actions YAML workflow files
- **Installation**: 
  - Windows: `choco install yq` or download from GitHub releases
  - Linux: `sudo apt-get install yq` or `snap install yq`
  - macOS: `brew install yq`
- **Usage**: Extract jobs, matrix configurations, dependencies, container requirements
- **Example commands**:
  ```bash
  # Extract all jobs
  yq '.jobs' .github/workflows/ci.yml
  
  # Extract matrix configurations
  yq '.jobs.build.strategy.matrix.include[]' .github/workflows/ci.yml
  
  # Extract container requirements
  yq '.jobs.build.strategy.matrix.include[].container' .github/workflows/ci.yml
  
  # Extract job dependencies
  yq '.jobs.build.needs' .github/workflows/ci.yml
  ```

#### `act`
- **Purpose**: Execute GitHub Actions workflows locally in Docker containers
- **Installation**:
  - Windows: `choco install act-cli` or download from GitHub releases
  - Linux: Download binary or use package manager
  - macOS: `brew install act`
- **Key flags**:
  - `-W <workflow-file>`: Specify workflow file
  - `-j <job-name>`: Target specific job
  - `--matrix <key>:<value>`: Filter matrix entries (can use multiple times)
  - `-P <runner>=<image>`: Use custom Docker image for runner
  - `--pull=false`: Don't pull images from registry
  - `--action-offline-mode`: Use cached actions only
  - `--dryrun`: Preview without executing
- **Example commands**:
  ```bash
  # Run specific job with matrix filter
  act -W .github/workflows/ci.yml \
      -j build \
      --matrix compiler:gcc \
      --matrix version:15 \
      -P ubuntu-latest=my-image:tag \
      --pull=false
  
  # List available jobs
  act --list
  
  # Dry run to preview
  act --dryrun --matrix compiler:gcc
  ```

### B. MCP Integration

#### MCP Server Endpoints

**`analyze_workflow`**
- **Purpose**: Execute Step 1 - Analyze workflow files and extract configuration
- **Input**: 
  - `workflow_file`: Path to workflow file (e.g., `.github/workflows/ci.yml`)
  - `event`: Git event name (e.g., `push`, `pull_request`)
- **Output**: 
  - `jobs`: List of jobs with their configurations
  - `matrix_entries`: All matrix combinations
  - `dependencies`: Job dependency graph

**`run_local_ci`**
- **Purpose**: Execute Steps 1-4 - Analyze workflows and trigger parallel job execution
- **Input**:
  - `workflow_file`: Path to workflow file
  - `event`: Git event name
  - `config`: Configuration object (optional)
    - `jobs`: List of job names to run
    - `matrix_filters`: Object with key-value pairs to filter matrix (e.g., `{"compiler": "gcc", "version": "15"}`)
    - `max_parallel`: Maximum concurrent jobs
    - `job_priorities`: Object mapping job names to priority values (e.g., `{"build": 1, "changelog": 2}`)
      - Lower number = higher priority (1 is highest)
      - If not specified, priorities extracted from workflow file or assigned default values
- **Output**:
  - `execution_id`: Unique identifier for this execution
  - `status_url`: URL to check execution status

**`get_status`**
- **Purpose**: Get execution status (Step 4 progress)
- **Input**: `execution_id`
- **Output**:
  - `progress`: Overall progress (e.g., `25/56 jobs completed`)
  - `completed_jobs`: List of completed jobs with results
  - `failed_jobs`: List of failed jobs with error messages
  - `running_jobs`: List of currently running jobs
  - `pending_jobs`: List of pending jobs

**`get_logs`**
- **Purpose**: Get logs for specific job
- **Input**:
  - `execution_id`
  - `job_name`: Name of the job
  - `matrix_entry`: Matrix entry identifier (optional)
- **Output**: Job execution logs

**`cancel_execution`**
- **Purpose**: Cancel running execution
- **Input**: `execution_id`
- **Output**: Cancellation status

#### Example MCP Request

```json
{
  "tool": "run_local_ci",
  "input": {
    "workflow_file": ".github/workflows/ci.yml",
    "event": "push",
    "config": {
      "jobs": ["build"],
      "matrix_filters": {
        "compiler": "gcc",
        "version": "15"
      },
      "max_parallel": 20
    }
  }
}
```

#### Async Operations

- Long-running executions return immediately with `execution_id`
- Client polls `get_status` endpoint for updates
- Results available via `get_status` and `get_logs` endpoints

#### Running the MCP server

The Local CI MCP server is implemented in `localci.mcp_server` and exposes the five tools above over **stdio** (so an MCP client spawns the server process and communicates via stdin/stdout).

- **Entry point**: From the project root (or `cli/`), run:
  - `localci-mcp` (if the package is installed), or
  - `python -m localci.mcp_server`
- **Config**: The server uses the same config as the CLI (`.localci.yml` discovered from the workflow file path or current directory). For `run_local_ci`, the workflow file path is resolved relative to the current working directory or as an absolute path.
- **Execution registry**: Running executions are tracked in memory so `get_status` and `cancel_execution` can target the correct run. After a run finishes, status and logs are read from the logs directory (same as CLI: `last-status.json`, `{execution_id}.json`).

### C. Setup

#### Prerequisites

1. **Docker Desktop** (Windows)
   - Install Docker Desktop with WSL2 backend
   - Enable Windows containers for Windows job testing
   - Enable Linux containers for Ubuntu job testing

2. **`yq`**: Install via package manager or download binary
   - Verify: `yq --version`

3. **`act`**: Install via package manager or download binary
   - Verify: `act --version`

#### Image Storage

- Store pre-built Docker images as `.tar` files in local directory
- Recommended structure: `images/<project>/<os-version>-<variant>.tar`
- Use `docker save -o <name>.tar <image>:<tag>` to create
- Use `docker load -i <name>.tar` to load
- Maintain image registry/index (JSON/YAML file) for matching

**Image Registry Format**

Create `image-registry.yml` to track available images:

```yaml
version: 1.0
images:
  - name: beast2-ubuntu-25.04-base
    file: images/beast2/ubuntu-25.04-base.tar
    docker_tag: beast2-ubuntu-25.04-base:latest
    os: ubuntu:25.04
    architecture: x86_64
    packages:
      - build-essential
      - libssl-dev
      - zlib1g-dev
      - libbrotli-dev
      - libpsl-dev
      - cmake
      - git
      - ccache
    compilers:
      - gcc-13
      - g++-13
    size_mb: 1024
    created: 2026-01-14T10:00:00Z
    last_used: 2026-01-14T15:30:00Z
    usage_count: 45
    
  - name: beast2-ubuntu-25.04-x86
    file: images/beast2/ubuntu-25.04-x86.tar
    docker_tag: beast2-ubuntu-25.04-x86:latest
    os: ubuntu:25.04
    architecture: i386
    packages:
      - build-essential
      - libssl-dev:i386
      - zlib1g-dev:i386
      - gcc-multilib
      - g++-multilib
    size_mb: 1280
    created: 2026-01-14T11:00:00Z
    last_used: 2026-01-14T14:20:00Z
    usage_count: 18
```

**Image Registry Operations:**
```bash
# Load image from tar file
docker load -i images/beast2/ubuntu-25.04-base.tar

# Verify loaded image
docker images | grep beast2

# Tag for `act` usage
docker tag beast2-ubuntu-25.04-base:latest catthehacker/ubuntu:act-25.04

# Save new/updated image
docker save -o images/beast2/ubuntu-25.04-base.tar beast2-ubuntu-25.04-base:latest
```

#### Configuration File

Create `local-ci-config.yml`:

```yaml
# Configuration for local CI execution
jobs:
  - name: build
    enabled: true
    priority: 1  # Higher priority = execute first (1 is highest)
    matrix_filters:
      - compiler: gcc
        version: 15
      - container: ubuntu:25.04
    max_parallel: 20
  
  - name: changelog
    enabled: true
    priority: 2  # Lower priority, waits for priority 1 jobs
  
  - name: antora
    enabled: false
    priority: 3

# Global settings
max_parallel_jobs: 20
resource_limits:
  cpu_per_job: 2
  memory_per_job: 4GB
  disk_per_job: 10GB
  cpu_threshold: 90  # Pause new jobs if CPU usage exceeds this percentage
  memory_threshold: 85  # Pause new jobs if memory usage exceeds this percentage
  disk_min_free_gb: 10  # Minimum free disk space in GB before warning
```

### D. Image Matching Algorithm

The algorithm uses a **two-mark system**: essential marks for infrastructure requirements and extra marks for packages/tools.

**Key principle**: Images are evaluated on essential marks first, then extra marks. This enables both exact matching and intelligent base image selection for new image creation.

**Mark Categories:**

**Essential Marks (Infrastructure - Maximum 100 points):**

Extracted from matrix entry:
- OS type, version, and architecture (combined): from `container: "ubuntu:25.04"` and `x86: true/false` → ubuntu:25.04+x86_64 or ubuntu:25.04+i386
- Compiler family and version: from `compiler: "gcc"`, `version: "15"` → gcc-15

Essential marks are calculated sequentially - if any earlier check fails, stop:

1. **Check OS+version+architecture match** (most critical, combined check):
   - OS type, version, and architecture must ALL match together
   - If OS+version+architecture does NOT match → **Essential mark = 0** (stop calculation)
   - If OS+version+architecture matches → Essential mark = 70, continue to step 2
   - Example: ubuntu:25.04+x86_64 ≠ ubuntu:24.04+x86_64 → Essential mark = 0 (version differs)
   - Example: ubuntu:25.04+x86_64 ≠ ubuntu:25.04+i386 → Essential mark = 0 (architecture differs)
   - Example: ubuntu:25.04+x86_64 ≠ debian:12+x86_64 → Essential mark = 0 (OS type differs)

2. **Check Compiler match** (if OS+version+architecture matched):
   - If Compiler does NOT match → Essential mark = 70 (OS+version+arch matched, but stop here)
   - If Compiler matches → Essential mark = 70 + 30 = 100
   - Example: OS+version+arch matched but gcc-15 ≠ gcc-14 → Essential mark = 70

**Essential marks possible values: 0, 70, 100**
- **Essential mark = 0**: OS+version+architecture mismatch (incompatible, cannot use as base)
- **Essential mark = 70**: OS+version+architecture match, but compiler differs (can use as base, upgrade compiler)
- **Essential mark = 100**: All infrastructure matches exactly (full match)

**Extra Marks (Packages/Tools - Maximum 100+ points):**

Extracted from matrix entry:
- Required packages: from `install: "gcc-15-multilib libssl-dev zlib1g-dev"`
- Build tools: from `build-cmake: true` → cmake required

Scoring:
1. **Required Packages Present**: +10 points per package
   - Typical: 5-10 packages = 50-100 points

2. **Build Tools Present**: +20 points per tool
   - cmake, ninja, ccache = up to 60 points

**Extra marks range: 0-160+ points** (but only evaluated if essential marks > 0)

**Selection Algorithm:**

1. **If any images have full essential marks (= 100)**:
   - Select the one with **highest extra marks**
   - Use this image

2. **If NO images have full essential marks**:
   - Select the image with **highest essential marks** (closest match)
   - Create new image using this as base
   - Install/upgrade to meet full essential requirements
   - Install all required packages

**Tie-Breaking (same total marks):**
- Prefer most recently used (`last_used` timestamp)
- Prefer highest usage count (`usage_count`)
- Prefer smallest size (`size_mb`)

**Algorithm Summary:**

```
For each job (matrix_entry):
  1. Extract requirements:
     - Essentials: OS+version+architecture, compiler+version
     - Extras: packages, build tools
  
  2. Evaluate all images in registry:
     For each image:
       Calculate essential marks (conditional, by order):
         essential_mark = 0
         IF OS+version+architecture matches (all together):
           essential_mark = 70
           IF Compiler matches:
             essential_mark = 100
         ELSE:
           essential_mark = 0 (stop, cannot use)
       
       Calculate extra marks (if essential_mark > 0):
         + Packages: 10 each
         + Tools: 20 each
  
  3. Decision:
     IF any images have essential marks = 100:
       → Select image with highest extra marks
       → Use this image
     
     ELSE (no images have essential marks = 100):
       → Select image with highest essential marks as base
       → Create new image from this base
       → Upgrade/install missing essentials + all packages
       → Save new image to registry
```

### E. New Image Creation Process

When no image has full essential marks (= 100), a new image must be created. The image with the highest essential marks is used as the base to minimize build time.

**E.1. Select Base Image**

If no images have essential marks = 100, select base by highest essential marks:

1. **Essential mark = 0** (OS+version+architecture mismatch):
   - Cannot use as base, start from scratch
   - Pull official base matching job requirements (OS, version, architecture)

2. **Essential mark = 70** (OS+version+architecture match, compiler differs):
   - Best case for base selection
   - OS type, version, and architecture all match
   - Only need to install/upgrade compiler
   - Fastest build time (reuse entire OS+architecture setup)

3. **Select image with highest essential marks**:
   - Prefer 70 over 0 (can reuse OS+version+architecture setup)
   - If tie at 70, use tie-breaking (recently used, usage count, size)

**E.2. Build New Image**

When building new image (essential marks < 100):

**If base has essential marks = 70:**
```dockerfile
# Load base image with matching OS+version+architecture
docker load -i <base-image>.tar

# Create Dockerfile to upgrade compiler only
FROM <base-image>:latest

# Install required compiler
RUN apt-get update && apt-get install -y gcc-15 g++-15
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-15 100

# Install missing packages
RUN apt-get install -y libssl-dev zlib1g-dev cmake

# Configure environment
ENV CC=gcc-15
ENV CXX=g++-15

# Build and save
docker build -t <new-image>:latest .
docker save -o <new-image>.tar <new-image>:latest
```

**If no base available (essential marks = 0):**
```dockerfile
# Pull official base matching OS+version+architecture
docker pull ubuntu:25.04  # or i386/ubuntu:25.04 for x86

# Create Dockerfile with all requirements
FROM ubuntu:25.04

# Install compiler
RUN apt-get update && apt-get install -y gcc-15 g++-15

# Install all packages
RUN apt-get install -y libssl-dev zlib1g-dev cmake ninja-build

# Configure environment
ENV CC=gcc-15
ENV CXX=g++-15

# Build and save
docker build -t <new-image>:latest .
docker save -o <new-image>.tar <new-image>:latest
```

**E.3. Update Image Registry**

After creating new image, update registry:

```yaml
# Add to image-registry.yml
- name: <new-image-name>
  file: images/<project>/<new-image-name>.tar
  docker_tag: <new-image-name>:latest
  os: ubuntu:25.04
  architecture: x86_64
  compilers:
    - gcc-15
  packages:
    - libssl-dev
    - zlib1g-dev
    - cmake
  size_mb: 1200
  created: <timestamp>
  last_used: <timestamp>
  usage_count: 0
```

**E.4. Image Naming Convention**

New images should follow naming pattern: `<project>-<os>-<variant>.tar`

Examples:
- `beast2-ubuntu-25.04-gcc15.tar` (specific compiler)
- `beast2-ubuntu-25.04-clang18-asan.tar` (compiler + variant)
- `beast2-ubuntu-24.04-x86.tar` (specific architecture)

### F. Host Platform Compatibility

**Windows Host:**
- Windows containers: Run natively
- Linux containers: Run via Docker Desktop (WSL2 backend)
- Parallel execution: Both container types can run simultaneously
- macOS containers: Not supported (macOS not containerized)

**Linux Host:**
- Linux containers: Run natively
- Windows containers: Not supported (requires Windows host)
- macOS containers: Not supported

**macOS Host:**
- Linux containers: Run via Docker Desktop
- Windows containers: Not supported
- macOS containers: Not supported (requires full VM)

**Recommendation**: Use Windows host for maximum compatibility (supports both Windows and Linux containers).

### G. Benefits and Limitations

#### Benefits

- **Faster iteration**: 50-80% faster than waiting for GitHub CI
- **Offline testing**: No network dependency for image loading (after initial setup)
- **Selective testing**: Run specific jobs/matrix entries without running full suite
- **Parallel execution**: Run ~20 jobs simultaneously (limited by host resources)
- **Cost savings**: No GitHub Actions minutes usage
- **Debugging**: Direct access to containers and logs for troubleshooting

#### Limitations

- **macOS tests**: Skipped (macOS containers not supported on any host)
- **Windows containers**: Require Windows host with Docker Desktop
- **Parallelism**: Limited by host resources (~20 concurrent jobs typical)
- **Image storage**: Requires disk space for pre-built images (10-50GB typical)
- **Initial setup**: Time required to build and save initial image set
- **Resource intensive**: Requires significant CPU, memory, and disk resources

### H. Resource Requirements

**Minimum Requirements:**
- CPU: 8 cores (for ~20 parallel jobs)
- RAM: 32GB (4GB per job × 8 concurrent)
- Disk: 100GB (for images, containers, build artifacts)
- Docker: Docker Desktop with WSL2 (Windows) or Docker Engine (Linux)

**Recommended Requirements:**
- CPU: 16+ cores
- RAM: 64GB+
- Disk: 200GB+ (SSD recommended)
- Network: Fast local storage for image loading

### I. Future Scalability

The system can be extended to scale beyond local host limitations using cloud infrastructure and container orchestration:

#### Cloud-Based Execution

**Cloud Storage for Images:**
- Store pre-built Docker images in cloud object storage (AWS S3, Azure Blob Storage, Google Cloud Storage)
- Download images on-demand to cloud compute instances
- Reduce local storage requirements
- Enable sharing images across multiple developers/teams

**Cloud Compute Instances:**
- Run CI jobs on cloud VMs (AWS EC2, Azure VMs, Google Compute Engine)
- Scale compute resources based on workload
- Pay-per-use model for occasional large test runs
- Support for larger parallel execution (100+ jobs)

#### Kubernetes Orchestration

**Kubernetes Cluster:**
- Deploy test orchestrator as Kubernetes controller
- Run each job as a Kubernetes Pod
- Automatic scaling based on queue length
- Resource management via Kubernetes resource limits

**Benefits:**
- **Horizontal scaling**: Add worker nodes to increase capacity
- **High availability**: Automatic pod restart on failures
- **Resource efficiency**: Better utilization of cluster resources
- **Parallel execution**: Run 100+ jobs simultaneously across cluster
- **Multi-platform**: Support for mixed Windows/Linux node pools

**Architecture:**
- **Control Plane**: MCP server + orchestrator controller
- **Worker Nodes**: Run `act` containers in Kubernetes pods
- **Image Registry**: Container registry (Docker Hub, GitHub Container Registry, private registry)
- **Storage**: Persistent volumes for image cache and artifacts

**Implementation Considerations:**
- Replace Docker API calls with Kubernetes API
- Use Kubernetes Jobs for one-time test executions
- Use ConfigMaps/Secrets for configuration management
- Implement custom Kubernetes operator for workflow orchestration
- Use Kubernetes CronJobs for scheduled test runs

**Migration Path:**
1. **Phase 1**: Local execution (current implementation)
2. **Phase 2**: Hybrid - local + cloud storage for images
3. **Phase 3**: Cloud compute instances for heavy workloads
4. **Phase 4**: Full Kubernetes deployment for enterprise scale

### J. Example Workflow Analysis

**Beast2 CI Workflow Example:**
- **Total jobs**: 4 (runner-selection, build, changelog, antora)
- **Build job matrix**: 56 configurations
  - Windows: 7 variants
  - macOS: 5 variants (skipped in local CI - not containerizable)
  - Linux: 44 variants (primary target for local CI)
- **Total job instances**: 61 (1 + 56 + 1 + 3)

**Matrix breakdown:**
- GCC versions: 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
- Clang versions: 3.9, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20
- Container images: ubuntu:18.04, ubuntu:20.04, ubuntu:22.04, ubuntu:24.04, ubuntu:25.04
- Variants: Standard, x86, ASAN, UBSAN, Coverage, Time-trace
