"""Queue and orchestration data models.

QueuedJob, JobEvent, and related enums used by the priority queue
and parallel execution manager. Workflow types (MatrixEntry, etc.)
live in workflow.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from localci.core.workflow import MatrixEntry


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class QueuedJobStatus(Enum):
    """Lifecycle status of a job in the queue."""

    QUEUED = "queued"
    WAITING_DEPS = "waiting_deps"
    WAITING_PRIORITY = "waiting_priority"
    READY = "ready"
    PREPARING = "preparing"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"


class JobEventType(Enum):
    """Event types emitted by the queue and orchestrator for progress tracking."""

    JOB_QUEUED = "job_queued"
    JOB_READY = "job_ready"
    JOB_PREPARING = "job_preparing"
    JOB_STARTED = "job_started"
    JOB_CANCELLED = "job_cancelled"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_TIMEOUT = "job_timeout"
    JOB_OUTPUT = "job_output"
    RESOURCE_WARNING = "resource_warning"
    PRIORITY_LEVEL_COMPLETE = "priority_level_complete"
    ALL_COMPLETE = "all_complete"


class PlatformOutcome(str, Enum):
    """How a queued job should be handled based on platform config."""

    RUN = "run"
    FAIL = "fail"
    SKIP = "skip"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class QueuedJob:
    """A single job (job_id + matrix entry) in the priority queue."""

    job_id: str
    matrix_entry: MatrixEntry
    priority: int
    dependencies: list[str]  # queue_key of dependent jobs
    image_tag: str | None = None
    base_image_tag: str | None = (
        None  # When needs_build, tag of base image to build from
    )
    needs_build: bool = False
    platform_outcome: PlatformOutcome = PlatformOutcome.RUN
    status: QueuedJobStatus = field(default=QueuedJobStatus.QUEUED)

    @property
    def queue_key(self) -> str:
        """Unique key for this job in the queue (job_id:entry_index)."""
        return f"{self.job_id}:{self.matrix_entry.index}"


@dataclass
class JobEvent:
    """Event emitted by the queue for observers (e.g. progress tracker)."""

    event_type: JobEventType
    job: QueuedJob
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = field(default_factory=datetime.now)
