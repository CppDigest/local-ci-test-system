WORK ITEM
Owner: Brad
Date: 2026-02-14
Status: done
Time Spent (today): 0h
Time Spent (total): TBD

Repo/Area: CppDigest/local-ci-test-system, cppalliance/capy
GitHub Issues: TBD
GitHub PRs:
Related Links: https://github.com/CppDigest/local-ci-test-system/blob/37d41b5cac3bb9e64cbafe5298038c61dc1ff8ca/Design%20Guide.md
Invoice Notes:
Tags: local-ci, capy, orchestrator, parallel, priority-queue, progress, phase-3
---
Title: Local CI Phase 3 - Parallel Execution and Orchestration

## Overview

Phase 3 builds upon the Phase 1 foundation (CLI, Workflow Analyzer, Job Executor, Docker Images) to enable **coordinated parallel execution** of multiple CI jobs. Where Phase 1 can execute individual jobs sequentially, Phase 3 introduces the intelligence layer that runs ~20 jobs simultaneously with priority-based scheduling, resource management, and real-time progress tracking.

**Prerequisite**: Phase 1 components (Issues 1, 2, 5, 12) must be functional.

**Target**: Execute all 10 Linux capy matrix entries in parallel, completing in ~1-2 minutes instead of 12-15 minutes.

---

## Architecture Context

```
┌─────────────────────────────────────────────────────────────────┐
│                      MCP Server Interface                        │
│  analyze_workflow | run_local_ci | get_status | get_logs        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    Test Orchestrator (PHASE 3)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Priority     │  │ Parallel     │  │ Real-time Progress    │  │
│  │ Job Queue    │  │ Execution    │  │ Tracking              │  │
│  │ (Issue 6)    │  │ Manager      │  │ (Issue 8)             │  │
│  │              │  │ (Issue 7)    │  │                       │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│         │                 │                       │              │
│         └─────────────────┼───────────────────────┘              │
└─────────────────────┬─────┘─────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ CI Workflow   │ │ Image Mgmt    │ │ Job Executor  │
│ Analyzer      │ │ System        │ │ (act)         │
│ (Phase 1)     │ │ (Phase 1)     │ │ (Phase 1)     │
└───────────────┘ └───────────────┘ └───────────────┘
```

Phase 3 components sit between the MCP/CLI interface and the Phase 1 execution layer, coordinating when and how jobs run.

---

## Phase 3 Components

### Issue 6: Priority-Based Job Queue

**Scope**: Manage job execution order with priorities and dependency resolution.

**Key Responsibilities**:
- Priority queue backed by a heap data structure
- Job dependency graph resolution (topological sort)
- Priority extraction from configuration and workflow
- Queue state management (enqueue, dequeue, peek, reorder)
- Priority constraints: higher-priority jobs must complete before lower-priority jobs start

**Design Reference**: Design Guide Step 2 (Determine Test List) and Step 4.1 (priority-ordered queue)

**Priority Rules** (from Design Guide):
- Jobs with higher priority (lower number) must complete before lower priority jobs can start
- Within the same priority level, jobs can run in parallel up to the parallel limit
- Priority can be extracted from workflow file or assigned via configuration

**Dependencies**: Issue 2 (Workflow Analyzer for MatrixEntry data)
**Blocks**: Issue 7 (Parallel Execution Manager consumes the queue)

---

### Issue 7: Parallel Execution Manager

**Scope**: Run multiple jobs concurrently with resource-aware scheduling.

**Key Responsibilities**:
- Configurable parallelism limit (~20 jobs concurrently)
- Resource monitoring (CPU, memory, disk thresholds)
- Priority-based scheduling with dependency enforcement
- Per-job lifecycle: image preparation → act execution → cleanup
- Job completion handling and next-job dispatch
- Graceful cancellation and error recovery

**Design Reference**: Design Guide Step 4 (Execute Jobs with Parallel Control), sections 4.1-4.3

**Execution Flow** (from Design Guide):
1. Image Preparation (load or build from execution plan)
2. Execute `act` command with output capture
3. Cleanup: extract results, clean containers, unload images

**Dependencies**: Issues 5 (Job Executor), 6 (Priority Queue), 3 (Image Registry)
**Blocks**: Issue 8 (Progress Tracking observes the manager)

---

### Issue 8: Real-time Progress Tracking

**Scope**: Monitor and report execution progress with a rich terminal UI.

**Key Responsibilities**:
- Progress aggregation: X/Y jobs completed, estimated time remaining
- Per-job status tracking: pending → preparing → running → passed/failed/timeout
- Live terminal UI with Rich Live display
- Summary report generation (text, JSON)
- Event-driven updates via callback/observer pattern
- MCP-compatible status reporting

**Design Reference**: Design Guide Step 4.4 (Progress Tracking) and Appendix B (get_status endpoint)

**Dependencies**: Issue 7 (Parallel Execution Manager emits events)
**Blocks**: Issue 15 (MCP Server consumes progress data)

---

## Data Flow

```
                    ┌──────────────────┐
                    │ Workflow Analyzer │
                    │ (Phase 1)        │
                    └────────┬─────────┘
                             │ list[MatrixEntry]
                             ▼
┌────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Configuration  │──►│ Priority Queue   │   │ Image Registry   │
│ (.localci.yml) │   │ (Issue 6)        │   │ (Phase 1)        │
└────────────────┘   └────────┬─────────┘   └────────┬─────────┘
                              │ QueuedJob             │ MatchResult
                              ▼                       ▼
                    ┌──────────────────────────────────┐
                    │   Parallel Execution Manager     │
                    │   (Issue 7)                      │
                    │                                  │
                    │   ┌─ Worker 1: act process ──┐   │
                    │   ├─ Worker 2: act process ──┤   │
                    │   ├─ Worker 3: act process ──┤   │
                    │   └─ Worker N: act process ──┘   │
                    └────────┬─────────────────────────┘
                             │ JobEvent stream
                             ▼
                    ┌──────────────────┐
                    │ Progress Tracker │
                    │ (Issue 8)        │
                    │                  │
                    │ ┌──────────────┐ │
                    │ │ Rich Live UI │ │
                    │ └──────────────┘ │
                    │ ┌──────────────┐ │
                    │ │ Summary Rpt  │ │
                    │ └──────────────┘ │
                    └──────────────────┘
```

---

## Shared Data Models

Phase 3 introduces several shared data models used across all three issues:

```python
# localci/core/models.py (additions for Phase 3)

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Any


class JobPriority(Enum):
    """Priority levels (lower number = higher priority)"""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 5
    LOW = 8
    BACKGROUND = 10


class QueuedJobStatus(Enum):
    """Status of a job in the queue"""
    QUEUED = "queued"
    WAITING_DEPS = "waiting_deps"
    WAITING_PRIORITY = "waiting_priority"
    READY = "ready"
    PREPARING = "preparing"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class JobEventType(Enum):
    """Events emitted during execution"""
    JOB_QUEUED = "job_queued"
    JOB_READY = "job_ready"
    JOB_PREPARING = "job_preparing"
    JOB_STARTED = "job_started"
    JOB_OUTPUT = "job_output"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_TIMEOUT = "job_timeout"
    JOB_CANCELLED = "job_cancelled"
    PRIORITY_LEVEL_COMPLETE = "priority_level_complete"
    ALL_COMPLETE = "all_complete"
    RESOURCE_WARNING = "resource_warning"


@dataclass
class QueuedJob:
    """A job ready for queue insertion"""
    job_id: str                         # e.g., "build"
    matrix_entry: 'MatrixEntry'
    priority: int                       # Lower = higher priority
    dependencies: list[str] = field(default_factory=list)
    status: QueuedJobStatus = QueuedJobStatus.QUEUED
    image_tag: Optional[str] = None     # Resolved by planner
    needs_build: bool = False           # From MatchResult

    @property
    def queue_key(self) -> str:
        """Unique key for this job in the queue"""
        return f"{self.job_id}:{self.matrix_entry.index}:{self.matrix_entry.name}"

    def __lt__(self, other: 'QueuedJob') -> bool:
        """For heap ordering: lower priority number = higher priority"""
        return self.priority < other.priority


@dataclass
class JobEvent:
    """Event emitted by the execution manager"""
    event_type: JobEventType
    job: QueuedJob
    timestamp: datetime = field(default_factory=datetime.now)
    data: dict = field(default_factory=dict)


@dataclass
class ResourceSnapshot:
    """System resource snapshot"""
    cpu_percent: float
    memory_percent: float
    disk_free_gb: float
    active_containers: int
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_healthy(self) -> bool:
        return (
            self.cpu_percent < 90
            and self.memory_percent < 85
            and self.disk_free_gb > 10
        )
```

---

## Phase 3 Deliverables Summary

Phase 3 is implemented: the priority queue (Issue 6), parallel execution manager (Issue 7), and real-time progress tracking (Issue 8) are wired into the CLI. `localci run` builds a queue from the workflow and config, runs jobs in parallel via the orchestrator (with per-job act cache and end-of-run container cleanup), and shows a Rich Live display plus post-run summary; `localci status` reads MCP-style status from `last-status.json` with `--format json` and `--follow`. Config and CLI support `--parallel`, `--timeout`, `keep_containers`, and `stop_on_first_failure`; resource limits (CPU/memory) pause dispatch when exceeded.

| Component | Status | Files |
|-----------|--------|-------|
| Priority Queue | **Done** | `cli/localci/core/queue.py` — PriorityJobQueue, PriorityConfig, PriorityRule, event emission, priority gating |
| Dependency Resolver | **Done** | `cli/localci/core/queue.py` — DependencyResolver (topological sort), integrated with queue |
| Queue Builder | **Done** | `cli/localci/core/queue_builder.py` — builds queue from WorkflowAnalyzer + config (platform/job/compiler/matrix filters, entries_include) |
| Parallel Execution Manager | **Done** | `cli/localci/core/orchestrator.py` — ParallelExecutionManager, OrchestratorConfig, ExecutionRun; ThreadPoolExecutor; per-job act cache; container cleanup at end only (no per-job cleanup to avoid killing parallel jobs) |
| Resource Monitor | **Done** | `cli/localci/utils/resources.py` — ResourceSnapshot, ResourceMonitor (optional psutil; CPU/memory/disk/container thresholds) |
| Progress Tracker | **Done** | `cli/localci/core/progress.py` — JobProgress, PriorityLevelProgress, ProgressTracker; event-driven state; current_step from act output |
| Live Terminal UI | **Done** | `cli/localci/core/progress.py` — Rich Live display (header, progress bar, priority levels, job table with Step column), 4 fps refresh |
| Summary Reporter | **Done** | `cli/localci/core/progress.py` — `print_summary()`; `cli/localci/core/results.py` — ExecutionSummary, summary_report() |
| MCP-style status (JSON) | **Done** | `cli/localci/core/progress.py` — `get_status_dict()`; `last-status.json` written during/after run |
| CLI Integration (run) | **Done** | `cli/localci/cli/run.py` — queue + orchestrator + tracker; live display; `last-status.json`; `tracker.print_summary()` |
| CLI Integration (status) | **Done** | `cli/localci/cli/status.py` — prefers `last-status.json`; `--format json`, `--follow`; _print_status_table with current_step |
| Data models (Phase 3) | **Done** | `cli/localci/core/models.py` — JobEvent (timestamp), JobEventType (JOB_TIMEOUT, etc.), QueuedJob (queue_key `job_id:index`) |

## Dependencies to Install (additions to Phase 1)

```
psutil>=5.9.0       # System resource monitoring
```

All other dependencies (click, rich, pydantic, pyyaml, docker) are already required by Phase 1.

---

## Integration with Phase 1

Phase 3 wires into Phase 1 components:

| Phase 1 Component | Phase 3 Consumer | Integration Point |
|-------------------|-----------------|-------------------|
| `WorkflowAnalyzer` | Priority Queue | `analyzer.analyze()` → `list[MatrixEntry]` → queue |
| `ImageMatcher` / `ExecutionPlanner` | Parallel Execution Manager | `planner.plan()` → `ExecutionPlan` → image loading |
| `JobExecutor` | Parallel Execution Manager | `executor.run()` called per worker |
| `DockerManager` | Parallel Execution Manager | Image load/unload, container cleanup |
| `LocalCIConfig` | Priority Queue, Execution Manager | Parallelism limits, platform filters, priorities |
| CLI `run` command | Orchestrator | `localci run --parallel 8` invokes orchestrator |
| CLI `status` command | Progress Tracker | `localci status --follow` renders live UI |

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Full Linux CI (10 jobs) | < 2 minutes with warm cache |
| Incremental build (10 jobs) | < 1 minute |
| Parallel utilization | > 80% CPU during execution |
| Priority enforcement | Higher priority jobs always finish first |
| Progress accuracy | Real-time within 1 second |
| Resource safety | No OOM kills, CPU < 95% |

---

## Implementation Priority Order

### Sprint 1: Queue and Scheduling
1. Issue 6: Priority-Based Job Queue
2. Issue 7: Parallel Execution Manager (core scheduling loop)

### Sprint 2: Execution and UI
3. Issue 7: Per-job lifecycle (image prep → execute → cleanup)
4. Issue 8: Real-time Progress Tracking
5. Wire into CLI `run` and `status` commands

---

## Next Steps

1. ~~Implement Issue 6 (Priority Queue)~~ — Done
2. ~~Implement Issue 7 (Parallel Manager)~~ — Done
3. ~~Implement Issue 8 (Progress Tracking)~~ — Done
4. Integration test: run all Linux capy jobs in parallel (manual/CI)
5. Performance benchmark against GitHub CI times
6. Issue 15: MCP Server endpoints (consume get_status / progress data) when ready
