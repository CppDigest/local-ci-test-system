"""Priority-based job queue for CI scheduling.

Implements the Design Guide's scheduling invariant: jobs with higher priority
(lower number) must all complete before any lower-priority job can start.
Within the same priority level, jobs are eligible to run concurrently.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from localci.core.models import (
    JobEvent,
    JobEventType,
    QueuedJob,
    QueuedJobStatus,
)

if TYPE_CHECKING:
    from localci.core.config import LocalCIConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Priority assignment
# ---------------------------------------------------------------------------


@dataclass
class PriorityRule:
    """Rule for assigning priority to a job/matrix entry.

    Rules are evaluated in order; first match wins.
    """

    pattern: str
    priority: int
    match_type: str = "name"  # "name", "compiler", "platform", "job_id"

    def matches(self, job: QueuedJob) -> bool:
        if self.match_type == "name":
            return fnmatch.fnmatch(job.matrix_entry.name, self.pattern)
        if self.match_type == "compiler":
            return job.matrix_entry.compiler.family.value == self.pattern
        if self.match_type == "platform":
            return job.matrix_entry.platform.value == self.pattern
        if self.match_type == "job_id":
            return job.job_id == self.pattern
        return False


@dataclass
class PriorityConfig:
    """Priority configuration from .localci.yml."""

    default_priority: int = 5
    rules: list[PriorityRule] = field(default_factory=list)
    explicit: dict[str, int] = field(default_factory=dict)

    def resolve_priority(self, job: QueuedJob) -> int:
        if job.matrix_entry.name in self.explicit:
            return self.explicit[job.matrix_entry.name]
        for rule in self.rules:
            if rule.matches(job):
                return rule.priority
        return self.default_priority

    @classmethod
    def from_config(cls, config: "LocalCIConfig") -> PriorityConfig:
        return cls(
            default_priority=5,
            explicit=dict(getattr(config, "priorities", {}) or {}),
            rules=[],
        )


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------


class CyclicDependencyError(Exception):
    """Circular dependency detected in job graph."""

    def __init__(self, job_id: str):
        super().__init__(f"Cyclic dependency detected involving job: {job_id}")
        self.job_id = job_id


class DependencyResolver:
    """Resolve job dependencies using topological sort.

    Handles GitHub Actions `needs` so jobs run in the correct order.
    """

    def __init__(self) -> None:
        self._graph: dict[str, list[str]] = {}
        self._reverse: dict[str, list[str]] = {}

    def add_job(self, job_id: str, needs: list[str]) -> None:
        self._graph[job_id] = list(needs)
        if job_id not in self._reverse:
            self._reverse[job_id] = []
        for dep in needs:
            if dep not in self._reverse:
                self._reverse[dep] = []
            self._reverse[dep].append(job_id)

    def resolve(self) -> list[str]:
        visited: set[str] = set()
        temp_visited: set[str] = set()
        order: list[str] = []

        def visit(jid: str) -> None:
            if jid in temp_visited:
                raise CyclicDependencyError(jid)
            if jid in visited:
                return
            temp_visited.add(jid)
            for dep in self._graph.get(jid, []):
                visit(dep)
            temp_visited.discard(jid)
            visited.add(jid)
            order.append(jid)

        for jid in self._graph:
            visit(jid)
        return order

    def get_dependencies(self, job_id: str) -> list[str]:
        return self._graph.get(job_id, [])

    def get_dependents(self, job_id: str) -> list[str]:
        return self._reverse.get(job_id, [])

    def all_dependencies_met(self, job_id: str, completed: set[str]) -> bool:
        return all(dep in completed for dep in self.get_dependencies(job_id))


# ---------------------------------------------------------------------------
# Priority job queue
# ---------------------------------------------------------------------------


class PriorityJobQueue:
    """Thread-safe priority queue for CI job scheduling.

    Higher-priority jobs (lower number) must all complete before any
    lower-priority job can start. Within the same priority level, jobs
    run in parallel.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, QueuedJob] = {}
        self._by_priority: dict[int, list[str]] = {}
        self._priority_levels: list[int] = []
        self._current_priority: Optional[int] = None
        self._completed_keys: set[str] = set()
        self._failed_keys: set[str] = set()
        self._running_keys: set[str] = set()
        self._dep_resolver = DependencyResolver()
        self._listeners: list[Callable[[JobEvent], None]] = []

    def add_listener(self, callback: Callable[[JobEvent], None]) -> None:
        self._listeners.append(callback)

    def _emit(self, event_type: JobEventType, job: QueuedJob, **data: object) -> None:
        event = JobEvent(event_type=event_type, job=job, data=dict(data))
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning("Event listener error: %s", e)

    def enqueue(self, job: QueuedJob) -> None:
        with self._lock:
            key = job.queue_key
            self._jobs[key] = job
            if job.priority not in self._by_priority:
                self._by_priority[job.priority] = []
            self._by_priority[job.priority].append(key)
            self._priority_levels = sorted(self._by_priority.keys())
            # Always point to highest priority (lowest number) so order of enqueue doesn't matter
            self._current_priority = self._priority_levels[0]
            self._dep_resolver.add_job(key, list(job.dependencies))
            job.status = QueuedJobStatus.QUEUED
            self._emit(JobEventType.JOB_QUEUED, job)
            logger.debug(
                "Enqueued: %s (priority=%s, deps=%s)",
                job.matrix_entry.name,
                job.priority,
                job.dependencies,
            )

    def enqueue_batch(self, jobs: list[QueuedJob]) -> None:
        for job in jobs:
            self.enqueue(job)
        logger.info(
            "Enqueued %d jobs across %d priority levels",
            len(jobs),
            len(self._priority_levels),
        )

    def next_ready(self) -> Optional[QueuedJob]:
        with self._lock:
            if self._current_priority is None:
                return None
            current_keys = self._by_priority.get(self._current_priority, [])
            for key in current_keys:
                job = self._jobs[key]
                if job.status != QueuedJobStatus.QUEUED:
                    continue
                if not self._dep_resolver.all_dependencies_met(
                    key, self._completed_keys
                ):
                    job.status = QueuedJobStatus.WAITING_DEPS
                    continue
                job.status = QueuedJobStatus.READY
                self._running_keys.add(key)
                self._emit(JobEventType.JOB_READY, job)
                return job
            return None

    def mark_completed(self, job: QueuedJob, success: bool = True) -> None:
        with self._lock:
            key = job.queue_key
            if success:
                job.status = QueuedJobStatus.PASSED
                self._completed_keys.add(key)
            else:
                job.status = QueuedJobStatus.FAILED
                self._failed_keys.add(key)
                self._completed_keys.add(key)
            self._running_keys.discard(key)
            self._check_priority_advance()

    def mark_running(self, job: QueuedJob) -> None:
        with self._lock:
            job.status = QueuedJobStatus.RUNNING
            self._emit(JobEventType.JOB_STARTED, job)

    def mark_preparing(self, job: QueuedJob) -> None:
        with self._lock:
            job.status = QueuedJobStatus.PREPARING
            self._emit(JobEventType.JOB_PREPARING, job)

    def cancel(self, key: str) -> bool:
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                return False
            if job.status not in (
                QueuedJobStatus.QUEUED,
                QueuedJobStatus.WAITING_DEPS,
                QueuedJobStatus.WAITING_PRIORITY,
            ):
                return False
            job.status = QueuedJobStatus.CANCELLED
            self._completed_keys.add(key)
            self._emit(JobEventType.JOB_CANCELLED, job)
            self._check_priority_advance()
            return True

    def cancel_all(self) -> int:
        count = 0
        with self._lock:
            for key, job in list(self._jobs.items()):
                if job.status in (
                    QueuedJobStatus.QUEUED,
                    QueuedJobStatus.WAITING_DEPS,
                    QueuedJobStatus.WAITING_PRIORITY,
                    QueuedJobStatus.READY,
                ):
                    job.status = QueuedJobStatus.CANCELLED
                    self._completed_keys.add(key)
                    count += 1
            if count > 0:
                self._check_priority_advance()
        return count

    def _check_priority_advance(self) -> None:
        if self._current_priority is None:
            return
        current_keys = self._by_priority.get(self._current_priority, [])
        terminal = (
            QueuedJobStatus.PASSED,
            QueuedJobStatus.FAILED,
            QueuedJobStatus.CANCELLED,
            QueuedJobStatus.SKIPPED,
            QueuedJobStatus.ERROR,
            QueuedJobStatus.TIMEOUT,
        )
        all_done = all(self._jobs[k].status in terminal for k in current_keys)
        if not all_done:
            return
        logger.info(
            "Priority level %s complete (%d jobs)",
            self._current_priority,
            len(current_keys),
        )
        if current_keys:
            self._emit(
                JobEventType.PRIORITY_LEVEL_COMPLETE,
                self._jobs[current_keys[0]],
                priority=self._current_priority,
                count=len(current_keys),
            )
        idx = self._priority_levels.index(self._current_priority)
        if idx + 1 < len(self._priority_levels):
            self._current_priority = self._priority_levels[idx + 1]
            logger.info("Advancing to priority level %s", self._current_priority)
            for key in self._by_priority.get(self._current_priority, []):
                j = self._jobs[key]
                if j.status == QueuedJobStatus.WAITING_PRIORITY:
                    j.status = QueuedJobStatus.QUEUED
        else:
            self._current_priority = None
            if self._jobs:
                first_key = next(iter(self._jobs))
                self._emit(JobEventType.ALL_COMPLETE, self._jobs[first_key])

    @property
    def is_empty(self) -> bool:
        return len(self._completed_keys) >= len(self._jobs)

    @property
    def is_done(self) -> bool:
        return self._current_priority is None and len(self._jobs) > 0

    @property
    def total_jobs(self) -> int:
        return len(self._jobs)

    @property
    def completed_count(self) -> int:
        return len(self._completed_keys)

    @property
    def running_count(self) -> int:
        return len(self._running_keys)

    @property
    def pending_count(self) -> int:
        return self.total_jobs - self.completed_count - self.running_count

    @property
    def passed_count(self) -> int:
        return sum(
            1 for j in self._jobs.values() if j.status == QueuedJobStatus.PASSED
        )

    @property
    def failed_count(self) -> int:
        return sum(
            1
            for j in self._jobs.values()
            if j.status
            in (
                QueuedJobStatus.FAILED,
                QueuedJobStatus.ERROR,
                QueuedJobStatus.TIMEOUT,
            )
        )

    @property
    def current_priority(self) -> Optional[int]:
        return self._current_priority

    def get_all_jobs(self) -> list[QueuedJob]:
        with self._lock:
            return list(self._jobs.values())

    def get_jobs_by_status(self, status: QueuedJobStatus) -> list[QueuedJob]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == status]

    def get_jobs_by_priority(self, priority: int) -> list[QueuedJob]:
        with self._lock:
            keys = self._by_priority.get(priority, [])
            return [self._jobs[k] for k in keys]

    def get_priority_summary(self) -> dict[int, dict[str, int]]:
        with self._lock:
            summary: dict[int, dict[str, int]] = {}
            for priority, keys in sorted(self._by_priority.items()):
                jobs = [self._jobs[k] for k in keys]
                pending_statuses = (
                    QueuedJobStatus.QUEUED,
                    QueuedJobStatus.WAITING_DEPS,
                    QueuedJobStatus.WAITING_PRIORITY,
                    QueuedJobStatus.READY,
                )
                summary[priority] = {
                    "total": len(jobs),
                    "passed": sum(
                        1 for j in jobs if j.status == QueuedJobStatus.PASSED
                    ),
                    "failed": sum(
                        1
                        for j in jobs
                        if j.status
                        in (
                            QueuedJobStatus.FAILED,
                            QueuedJobStatus.ERROR,
                            QueuedJobStatus.TIMEOUT,
                        )
                    ),
                    "running": sum(
                        1 for j in jobs if j.status == QueuedJobStatus.RUNNING
                    ),
                    "preparing": sum(
                        1 for j in jobs if j.status == QueuedJobStatus.PREPARING
                    ),
                    "pending": sum(
                        1 for j in jobs if j.status in pending_statuses
                    ),
                }
            return summary
