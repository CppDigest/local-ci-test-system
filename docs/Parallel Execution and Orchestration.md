# Parallel Execution and Orchestration — Feature Guide

This document explains how Local CI **runs multiple jobs in parallel**, how **priorities** and **progress tracking** work, and how to configure and interpret a run.

**See also:** [User Guide](../cli/USER_GUIDE.md) for commands and config; [Core Infrastructure](Core%20Infrastructure.md) for the executor and workflow analysis; [Design Guide](Design%20Guide.md) and [Preparation and Plan](Preparation%20and%20Plan.md) for development and implementation plan.

---

## Overview

When you run `localci run --platform linux`, Local CI does not run jobs one after another. It:

1. **Builds a queue** of jobs from the workflow matrix and your config (platform, job, and matrix filters).
2. **Assigns a priority** to each job (from config or defaults) so that more important jobs can run first.
3. **Runs many jobs at once** (e.g. 8 or 20) up to a configurable parallelism limit.
4. **Monitors resources** (CPU, memory, disk) and can pause starting new jobs when the system is under pressure.
5. **Shows live progress** in the terminal (job table, current step, per-job status) and writes a summary and status file at the end.

Together, these are the **orchestrator** and **parallel execution** features: they turn a list of matrix entries into a coordinated, parallel run with clear feedback.

---

## How It Fits in the Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                      CLI                                          │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    Orchestrator                                    │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│  │ Priority     │  │ Parallel     │  │ Progress Tracking    │   │
│  │ Job Queue    │  │ Execution    │  │ (live UI, summary)   │   │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘   │
│         │                 │                       │               │
└─────────┼─────────────────┼───────────────────────┼───────────────┘
          │                 │                       │
          ▼                 ▼                       ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ Workflow      │ │ Image         │ │ Job Executor   │
│ Analyzer      │ │ Registry      │ │ (act)          │
└───────────────┘ └───────────────┘ └───────────────┘
```

The orchestrator uses the workflow analyzer to get the job list, the image registry to resolve images, and the job executor to run each job. It feeds a **priority queue**, runs jobs in parallel with **resource awareness**, and drives **progress tracking** and the **summary report**.

---

## Feature 1: Priority-Based Job Queue

### What it does

Jobs are not run in an arbitrary order. They are placed in a **priority queue**: lower priority number means higher priority. Higher-priority jobs are run before lower-priority ones; within the same priority level, jobs can run in parallel up to the parallelism limit.

This lets you, for example, run quick “smoke” configurations first or ensure coverage jobs run early.

### How it works

- Each matrix entry is turned into a **queued job** with a **priority** (integer). Defaults can be derived from the workflow or config.
- The queue is ordered by priority. The orchestrator takes jobs from the queue and starts them until the parallelism limit is reached. When a job finishes, it starts the next available job that does not violate priority (higher-priority jobs must complete before lower-priority ones start, as per the design).
- Dependencies (e.g. `needs:` in the workflow) can be respected so that dependent jobs only run after their dependencies complete.

### Configuration

In `.localci.yml` you can set priorities by job name:

```yaml
priorities:
  "GCC 15: C++20": 1
  "Clang 20: C++20-23": 2
  "GCC 13: C++20 (coverage)": 3
```

Lower number = higher priority. Jobs not listed get a default priority (e.g. 5). The exact default and behavior are described in the User Guide and Design Guide.

---

## Feature 2: Parallel Execution Manager

### What it does

Runs **multiple jobs at the same time** instead of one by one. You set a **maximum number of concurrent jobs** (e.g. 8); the orchestrator starts that many jobs and, as each completes, starts the next from the queue (subject to priority and dependencies).

### How it works

- **Workers:** The orchestrator uses a thread pool (or similar). Each “slot” runs one job: resolve image, build act command (with cache mounts), run act, collect result.
- **Per-job lifecycle:** For each job, the orchestrator (1) ensures the image is available (load from tar or trigger build if configured), (2) resolves cache paths and optionally patches the workflow (e.g. inject cache mounts, bootstrap skip), (3) invokes the executor (act), (4) records the result and updates progress. Containers are cleaned up at the end of the run (not after each job) to avoid interfering with parallel act runs.
- **Resource monitoring:** Optional CPU/memory/disk checks can pause starting new jobs when the system is overloaded, then resume when resources are back under the configured limits.

### Configuration

```yaml
parallel:
  max_jobs: 8
  resource_limit:
    cpu_percent: 80
    memory_percent: 70
```

- **max_jobs:** Maximum number of jobs running at once.
- **resource_limit:** If resource monitoring is enabled, dispatch is paused when CPU or memory exceeds these percentages (and optionally when disk is low). Requires `psutil` for CPU/memory.

**CLI override:** `localci run --parallel 4` (or similar) overrides `max_jobs` for that run.

**Other execution options** (in `execution`):

- **timeout:** Per-job timeout in seconds.
- **keep_containers:** If true, containers are not removed after the run (useful for debugging).
- **stop_on_first_failure:** If true, the run stops as soon as one job fails (no new jobs started; already running jobs may still finish).

---

## Feature 3: Real-Time Progress Tracking

### What it does

While a run is in progress, Local CI shows:

- A **live-updating table** of jobs: status (pending, preparing, running, passed, failed, timeout), duration, and the **current step** (e.g. “Main Install packages”, “Main Boost B2 Workflow”) from act output.
- **Priority levels** and how many jobs in each level have completed.
- **Overall progress** (e.g. “5/10 jobs completed”).

When the run finishes, it prints a **summary**: total duration, pass/fail counts, and a table of each job with result and duration. It also writes machine-readable output (e.g. `last-run.json`, `last-status.json`) for use by `localci status`.

### How you use it

- **During run:** Just run `localci run ...`; the live display updates automatically. No extra flags needed.
- **After run:** `localci status` shows the status of the last run (or a specific execution by ID). Use `localci status --format json` for scriptable output. Logs for a specific job: `localci logs <job>` (e.g. by index or name).

### Status file

The orchestrator (or progress tracker) writes a status file (e.g. `last-status.json`) during and after the run. It includes execution ID, start time, duration, per-job status, and optional current step. Use `localci status` (or `localci status --format json`) to query run status without parsing the terminal.

---

## Data Flow (Conceptual)

```text
Workflow file + Config
        │
        ▼
Workflow Analyzer  ──►  list of MatrixEntry
        │
        ├──────────────────────┐
        ▼                      ▼
Config (filters,          Image Registry
priorities)                    │
        │                      │
        ▼                      ▼
Priority Queue  ◄────  QueuedJob (entry + image_tag + priority)
        │
        ▼
Orchestrator: for each slot, pop next job
        │
        ├──► Image prep (load/build if needed)
        ├──► Resolve cache paths, patch workflow
        ├──► Executor.run(act)
        ├──► Emit events (started, output, completed)
        └──► Update progress, write status
        │
        ▼
Progress Tracker  ──►  Live UI + Summary + last-status.json
```

---

## Summary of What You Can Configure

| Area | What you set | Where |
|------|----------------------|--------|
| Parallelism | Max concurrent jobs | `parallel.max_jobs` or `--parallel` |
| Resources | When to pause starting jobs | `parallel.resource_limit` (CPU, memory) |
| Priorities | Which jobs run first | `priorities` (job name → number) |
| Failure behavior | Stop on first failure | `execution.stop_on_first_failure` |
| Timeout | Per-job timeout (seconds) | `execution.timeout` |
| Containers | Keep containers after run | `execution.keep_containers` |

Details and defaults are in the [User Guide](../cli/USER_GUIDE.md).

---

## Success Criteria (Targets)

| Metric | Target |
|--------|--------|
| Full Linux CI (e.g. 10 jobs) | &lt; 2 minutes with warm cache |
| Incremental run | &lt; 1 minute |
| Priority | Higher-priority jobs finish before lower-priority ones start |
| Progress | Updates within about 1 second |
| Resource safety | No OOM; CPU kept under configured limit when monitoring is on |

---

## Reference

- **User Guide:** [cli/USER_GUIDE.md](../cli/USER_GUIDE.md) — Commands, config, troubleshooting.
- **Core Infrastructure:** [Core Infrastructure.md](Core%20Infrastructure.md) — Executor, analyzer, images.
- **Design Guide:** [Design Guide.md](Design%20Guide.md) — Architecture, queue, execution flow.
- **Preparation and Plan:** [Preparation and Plan.md](Preparation%20and%20Plan.md) — Implementation plan and issue breakdown (e.g. priority queue, parallel manager, progress tracking).
