"""Real-time progress tracking for the parallel execution manager.

Observes the orchestrator via job events and provides Rich Live display,
JSON status files, and post-execution summary reports.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from localci.core.executor import JobResult
from localci.core.models import (
    JobEvent,
    JobEventType,
    QueuedJobStatus,
)

if TYPE_CHECKING:
    from rich.live import Live

    from localci.core.orchestrator import ExecutionRun
    from localci.core.queue import PriorityJobQueue

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class JobProgress:
    """Tracked state for a single job."""

    key: str
    name: str
    index: int
    priority: int
    status: QueuedJobStatus = QueuedJobStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    exit_code: int | None = None
    error_message: str | None = None
    log_file: str | None = None
    current_step: str | None = (
        None  # From act output (e.g. "Run Main Clone Boost.Capy")
    )
    step_timings: list[tuple[str, float]] = field(
        default_factory=list
    )  # (step_name, duration_seconds)

    @property
    def elapsed(self) -> float:
        """Seconds since job started (or total if finished)."""
        if self.finished_at:
            return self.duration_seconds
        if self.started_at:
            return (datetime.now() - self.started_at).total_seconds()
        return 0.0

    @property
    def elapsed_display(self) -> str:
        """Human-readable elapsed time."""
        secs = self.elapsed
        if secs <= 0:
            return "-"
        if secs < 60:
            return f"{secs:.0f}s"
        minutes = int(secs // 60)
        remaining = secs % 60
        return f"{minutes}m {remaining:.0f}s"

    @property
    def status_icon(self) -> str:
        icons = {
            QueuedJobStatus.QUEUED: "◌",
            QueuedJobStatus.WAITING_DEPS: "◌",
            QueuedJobStatus.WAITING_PRIORITY: "◌",
            QueuedJobStatus.READY: "◎",
            QueuedJobStatus.PREPARING: "⟳",
            QueuedJobStatus.RUNNING: "●",
            QueuedJobStatus.PASSED: "✓",
            QueuedJobStatus.FAILED: "✗",
            QueuedJobStatus.TIMEOUT: "⏱",
            QueuedJobStatus.ERROR: "⚠",
            QueuedJobStatus.CANCELLED: "⊘",
            QueuedJobStatus.SKIPPED: "⊖",
        }
        return icons.get(self.status, "?")

    @property
    def status_style(self) -> str:
        """Rich style string for this status."""
        styles = {
            QueuedJobStatus.QUEUED: "dim",
            QueuedJobStatus.WAITING_DEPS: "dim",
            QueuedJobStatus.WAITING_PRIORITY: "dim",
            QueuedJobStatus.READY: "yellow",
            QueuedJobStatus.PREPARING: "yellow",
            QueuedJobStatus.RUNNING: "cyan bold",
            QueuedJobStatus.PASSED: "green",
            QueuedJobStatus.FAILED: "red bold",
            QueuedJobStatus.TIMEOUT: "red",
            QueuedJobStatus.ERROR: "red",
            QueuedJobStatus.CANCELLED: "dim strikethrough",
            QueuedJobStatus.SKIPPED: "dim",
        }
        return styles.get(self.status, "")

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            QueuedJobStatus.PASSED,
            QueuedJobStatus.FAILED,
            QueuedJobStatus.TIMEOUT,
            QueuedJobStatus.ERROR,
            QueuedJobStatus.CANCELLED,
            QueuedJobStatus.SKIPPED,
        )


@dataclass
class PriorityLevelProgress:
    """Aggregated progress for a priority level."""

    priority: int
    total: int = 0
    passed: int = 0
    failed: int = 0
    running: int = 0
    pending: int = 0
    cancelled: int = 0

    @property
    def complete(self) -> int:
        return self.passed + self.failed + self.cancelled

    @property
    def is_done(self) -> bool:
        return self.complete == self.total

    @property
    def status_icon(self) -> str:
        if self.is_done:
            return "✓" if self.failed == 0 else "✗"
        if self.running > 0:
            return "●"
        return "◌"

    @property
    def status_label(self) -> str:
        if self.is_done:
            return (
                "complete" if self.failed == 0 else f"complete ({self.failed} failed)"
            )
        if self.running > 0:
            return "running"
        return "pending"


# ---------------------------------------------------------------------------
# Progress tracker
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Track and display execution progress.

    Receives events from the Parallel Execution Manager and maintains
    an up-to-date view of all job states. Renders to the terminal
    via Rich Live display.

    Usage:
        queue = PriorityJobQueue()
        tracker = ProgressTracker(queue, ...)
        orchestrator.add_listener(tracker.on_event)
        tracker.start_live()
        # ... orchestrator runs ...
        tracker.stop_live()
        tracker.print_summary(execution_run)
    """

    def __init__(
        self,
        queue: PriorityJobQueue,
        workflow_name: str = "",
        workflow_file: str = "",
        platform: str = "linux",
        max_parallel: int = 8,
        status_file: Path | None = None,
    ):
        self.queue = queue
        self.workflow_name = workflow_name or ""
        self.workflow_file = workflow_file or ""
        self.platform = platform
        self.max_parallel = max_parallel
        self.status_file = Path(status_file) if status_file else None

        self._lock = threading.Lock()
        self._jobs: dict[str, JobProgress] = {}
        self._started_at: datetime | None = None
        self._execution_id: str | None = None
        self._live: Live | None = None
        self._completed_durations: list[float] = []
        self._last_status_write: float = 0.0
        self._status_write_interval: float = 1.0
        # Per-step runtime: key -> (current_step_name, start_time); closed on next step or job end
        self._step_start: dict[str, tuple[str, datetime]] = {}

    # -----------------------------------------------------------------------
    # Event handler (called by orchestrator)
    # -----------------------------------------------------------------------

    def on_event(self, event: JobEvent) -> None:
        """Process an event from the execution manager.

        Thread-safe: called from worker threads.
        """
        ts = event.timestamp or datetime.now()
        with self._lock:
            exec_id = event.data.get("execution_id")
            if exec_id:
                self._execution_id = exec_id

            job = event.job
            key = job.queue_key

            if key not in self._jobs:
                self._jobs[key] = JobProgress(
                    key=key,
                    name=job.matrix_entry.name,
                    index=job.matrix_entry.index,
                    priority=job.priority,
                )

            progress = self._jobs[key]

            if event.event_type == JobEventType.JOB_QUEUED:
                progress.status = QueuedJobStatus.QUEUED

            elif event.event_type == JobEventType.JOB_READY:
                progress.status = QueuedJobStatus.READY

            elif event.event_type == JobEventType.JOB_PREPARING:
                progress.status = QueuedJobStatus.PREPARING

            elif event.event_type == JobEventType.JOB_STARTED:
                progress.status = QueuedJobStatus.RUNNING
                progress.started_at = ts
                if self._started_at is None:
                    self._started_at = ts

            elif event.event_type == JobEventType.JOB_OUTPUT:
                line = event.data.get("line")
                if isinstance(line, str) and " Run " in line:
                    step = line.split(" Run ", 1)[-1].strip()
                    if step:
                        step_short = step[:50] if len(step) > 50 else step
                        # Close previous step and record duration
                        if key in self._step_start:
                            prev_name, prev_start = self._step_start.pop(key)
                            dur = (ts - prev_start).total_seconds()
                            progress.step_timings.append((prev_name, dur))
                        self._step_start[key] = (step, ts)
                        progress.current_step = step_short

            elif event.event_type == JobEventType.JOB_COMPLETED:
                if key in self._step_start:
                    prev_name, prev_start = self._step_start.pop(key)
                    progress.step_timings.append(
                        (prev_name, (ts - prev_start).total_seconds())
                    )
                progress.current_step = None
                progress.status = QueuedJobStatus.PASSED
                progress.finished_at = ts
                result = event.data.get("result")
                if isinstance(result, JobResult):
                    progress.duration_seconds = result.duration_seconds
                    progress.exit_code = result.exit_code
                    progress.log_file = (
                        str(result.log_file) if result.log_file else None
                    )
                self._completed_durations.append(progress.duration_seconds)

            elif event.event_type == JobEventType.JOB_FAILED:
                if key in self._step_start:
                    prev_name, prev_start = self._step_start.pop(key)
                    progress.step_timings.append(
                        (prev_name, (ts - prev_start).total_seconds())
                    )
                progress.current_step = None
                progress.status = QueuedJobStatus.FAILED
                progress.finished_at = ts
                result = event.data.get("result")
                if isinstance(result, JobResult):
                    progress.duration_seconds = result.duration_seconds
                    progress.exit_code = result.exit_code
                    progress.error_message = result.error_message
                    progress.log_file = (
                        str(result.log_file) if result.log_file else None
                    )
                self._completed_durations.append(progress.duration_seconds)

            elif event.event_type == JobEventType.JOB_TIMEOUT:
                if key in self._step_start:
                    prev_name, prev_start = self._step_start.pop(key)
                    progress.step_timings.append(
                        (prev_name, (ts - prev_start).total_seconds())
                    )
                progress.current_step = None
                progress.status = QueuedJobStatus.TIMEOUT
                progress.finished_at = ts
                progress.error_message = "Timed out"
                result = event.data.get("result")
                if isinstance(result, JobResult):
                    progress.duration_seconds = result.duration_seconds
                    progress.exit_code = result.exit_code
                    progress.log_file = (
                        str(result.log_file) if result.log_file else None
                    )
                self._completed_durations.append(progress.duration_seconds)

            elif event.event_type == JobEventType.JOB_CANCELLED:
                if key in self._step_start:
                    self._step_start.pop(key)
                progress.current_step = None
                progress.status = QueuedJobStatus.CANCELLED
                progress.finished_at = ts

        if self._live:
            try:
                self._live.update(self._render_live())
            except Exception as e:
                logger.debug("Live update error: %s", e)

        if self.status_file:
            now = time.time()
            if now - self._last_status_write >= self._status_write_interval:
                self._last_status_write = now
                self._write_status_file()

    def set_execution_id(self, execution_id: str) -> None:
        """Set execution ID (e.g. when run starts)."""
        with self._lock:
            self._execution_id = execution_id

    def _write_status_file(self) -> None:
        """Write current status to status_file (throttled)."""
        self._write_status_file_impl()

    def write_status_file(self) -> None:
        """Write current status to status_file unconditionally (e.g. at end of run)."""
        self._write_status_file_impl()

    def _write_status_file_impl(self) -> None:
        """Write current status to status_file atomically (temp file + os.replace)."""
        if not self.status_file:
            return
        try:
            data = self.get_status_dict()
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            dir_path = str(self.status_file.parent)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=dir_path,
                delete=False,
                suffix=".tmp",
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, self.status_file)
        except Exception as e:
            logger.debug("Could not write status file: %s", e)

    # -----------------------------------------------------------------------
    # Live display
    # -----------------------------------------------------------------------

    def start_live(self) -> None:
        """Start Rich Live display."""
        from rich.live import Live

        live = Live(
            self._render_live(),
            refresh_per_second=4,
            transient=True,
        )
        self._live = live
        live.start()

    def stop_live(self) -> None:
        """Stop Rich Live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def _render_live(self) -> Any:
        """Render the complete live display."""
        from rich.console import Group

        parts: list[Any] = []
        parts.append(self._render_header())
        parts.append(self._render_progress_bar())
        parts.append(self._render_priority_levels())
        parts.append(self._render_job_table())
        return Group(*parts)

    def _render_header(self) -> Any:
        """Render execution header panel."""
        from rich.panel import Panel

        with self._lock:
            total = len(self._jobs)
            elapsed = self._elapsed_display()
        exec_id = self._execution_id or "-"
        content = (
            f"Execution: {exec_id}\n"
            f"Workflow:  {self.workflow_file}\n"
            f"Platform:  {self.platform} ({total} jobs)\n"
            f"Parallel:  {self.max_parallel} workers\n"
            f"Elapsed:   {elapsed}"
        )
        return Panel(content, title="Local CI Execution", expand=True)

    def _render_progress_bar(self) -> Any:
        """Render progress bar with ETA."""
        from rich.text import Text

        with self._lock:
            total = len(self._jobs)
            completed = sum(1 for j in self._jobs.values() if j.is_terminal)
        eta = self._estimate_eta()
        pct = (completed / total * 100) if total > 0 else 0
        bar_filled = int(pct / 5)
        bar_empty = 20 - bar_filled
        bar = f"{'█' * bar_filled}{'░' * bar_empty}"
        eta_str = f"ETA: ~{eta:.0f}s" if eta > 0 else "ETA: -"
        return Text(
            f"\nProgress: {bar} {completed}/{total} ({pct:.0f}%)  "
            f"Elapsed: {self._elapsed_display()}  {eta_str}\n"
        )

    def _render_priority_levels(self) -> Any:
        """Render priority level summary."""
        from rich.text import Text

        levels = self._get_priority_levels()
        lines = []
        for level in levels:
            icon = level.status_icon
            label = level.status_label
            suffix = (
                f" ({level.complete}/{level.total}"
                + (" passed" if level.is_done and level.failed == 0 else "")
                + ")"
            )
            lines.append(f"Priority {level.priority} {icon} {label}{suffix}")
        return Text("\n".join(lines) + "\n")

    def _render_job_table(self) -> Any:
        """Render per-job status table with current step when running."""
        from rich.table import Table

        table = Table(expand=True)
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Idx", justify="right", style="dim", width=4)
        table.add_column("Job", style="bold", ratio=2)
        table.add_column("Status", width=14)
        table.add_column("Step", style="dim", ratio=2)
        table.add_column("Duration", justify="right", width=10)

        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: (j.priority, j.index),
            )

        for row_num, job in enumerate(jobs, start=1):
            status_text = f"{job.status_icon} {job.status.value}"
            step_text = job.current_step or "-"
            table.add_row(
                str(row_num),
                str(job.index),
                job.name,
                f"[{job.status_style}]{status_text}[/{job.status_style}]",
                step_text,
                job.elapsed_display,
            )
        return table

    # -----------------------------------------------------------------------
    # Summary report
    # -----------------------------------------------------------------------

    def print_summary(self, run: ExecutionRun) -> None:
        """Print post-execution summary report."""
        from rich.console import Console
        from rich.table import Table

        console = Console()

        console.print()
        console.print("═" * 64)
        console.print("                    Execution Summary", style="bold")
        console.print("═" * 64)

        duration = run.duration_seconds
        if duration < 60:
            dur_str = f"{duration:.1f}s"
        else:
            dur_str = f"{int(duration // 60)}m {duration % 60:.0f}s"

        result_style = "green bold" if run.all_passed else "red bold"
        result_text = (
            f"{run.passed}/{run.total} PASSED"
            if run.all_passed
            else f"{run.passed}/{run.total} PASSED, {run.failed} FAILED"
        )

        console.print(f"\nExecution ID: {run.execution_id}")
        console.print(f"Duration:     {dur_str}")
        console.print(f"Result:       [{result_style}]{result_text}[/{result_style}]")
        console.print()

        levels = self._get_priority_levels()
        for level in levels:
            style = "green" if level.failed == 0 else "red"
            suffix = f"  ← {level.failed} failure(s)" if level.failed > 0 else ""
            console.print(
                f"Priority {level.priority}: "
                f"[{style}]{level.passed}/{level.total} passed[/{style}]"
                f"{suffix}"
            )
        console.print()

        table = Table(expand=True)
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Idx", justify="right", style="dim", width=4)
        table.add_column("Job", style="bold", ratio=3)
        table.add_column("Result", width=14)
        table.add_column("Duration", justify="right", width=10)

        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: (j.priority, j.index),
            )

        for row_num, job in enumerate(jobs, start=1):
            result_str = f"{job.status_icon} {job.status.value}"
            table.add_row(
                str(row_num),
                str(job.index),
                job.name,
                f"[{job.status_style}]{result_str}[/{job.status_style}]",
                job.elapsed_display,
            )
        console.print(table)

        # Per-step runtime (longest job) — helps prioritize optimization
        jobs_with_steps = [j for j in jobs if j.step_timings]
        if jobs_with_steps:
            longest_job = max(jobs_with_steps, key=lambda j: j.duration_seconds)
            steps = longest_job.step_timings
            if steps:
                console.print(
                    "\n[bold]Per-step runtime[/bold] (longest job: "
                    f"[{longest_job.index}] {longest_job.name})"
                )
                step_table = Table(expand=False)
                step_table.add_column("Step", style="dim")
                step_table.add_column("Duration", justify="right")
                longest_step = max(steps, key=lambda x: x[1])
                for name, dur in steps:
                    step_dur_str = f"{dur:.1f}s"
                    if (name, dur) == longest_step:
                        step_table.add_row(
                            f"[bold]{name}[/bold]",
                            f"[bold]{step_dur_str}[/bold] ← longest",
                        )
                    else:
                        step_table.add_row(name, step_dur_str)
                console.print(step_table)
                console.print()

        failures = [
            j
            for j in jobs
            if j.status
            in (
                QueuedJobStatus.FAILED,
                QueuedJobStatus.ERROR,
                QueuedJobStatus.TIMEOUT,
            )
        ]
        if failures:
            console.print(f"\n[red bold]FAILURES ({len(failures)}):[/red bold]")
            console.print("─" * 64)
            for job in failures:
                console.print(f"\n[red bold][{job.index}] {job.name}[/red bold]")
                if job.exit_code is not None:
                    console.print(f"    Exit code: {job.exit_code}")
                console.print(f"    Duration:  {job.elapsed_display}")
                if job.error_message:
                    console.print(f"    Error:     {job.error_message}")
                if job.log_file:
                    console.print(f"    Log:       {job.log_file}")
        console.print("═" * 64)
        console.print()

    # -----------------------------------------------------------------------
    # JSON status (for localci status --format json)
    # -----------------------------------------------------------------------

    def get_status_dict(self) -> dict[str, Any]:
        """Get structured status for JSON output and status files."""
        with self._lock:
            jobs = list(self._jobs.values())

        completed = [j for j in jobs if j.status == QueuedJobStatus.PASSED]
        failed = [
            j
            for j in jobs
            if j.status
            in (
                QueuedJobStatus.FAILED,
                QueuedJobStatus.ERROR,
                QueuedJobStatus.TIMEOUT,
            )
        ]
        cancelled = [
            j
            for j in jobs
            if j.status in (QueuedJobStatus.CANCELLED, QueuedJobStatus.SKIPPED)
        ]
        running = [
            j
            for j in jobs
            if j.status in (QueuedJobStatus.RUNNING, QueuedJobStatus.PREPARING)
        ]
        pending = [
            j
            for j in jobs
            if j.status
            in (
                QueuedJobStatus.QUEUED,
                QueuedJobStatus.WAITING_DEPS,
                QueuedJobStatus.WAITING_PRIORITY,
                QueuedJobStatus.READY,
            )
        ]

        total = len(jobs)
        done = len(completed) + len(failed) + len(cancelled)

        result = {
            "progress": f"{done}/{total} jobs completed",
            "execution_id": self._execution_id,
            "total": total,
            "completed_count": done,
            "passed_count": len(completed),
            "failed_count": len(failed),
            "cancelled_count": len(cancelled),
            "running_count": len(running),
            "pending_count": len(pending),
            "elapsed_seconds": self._elapsed_seconds(),
            "eta_seconds": self._estimate_eta(),
            "completed_jobs": [
                {
                    "name": j.name,
                    "index": j.index,
                    "priority": j.priority,
                    "duration_seconds": j.duration_seconds,
                    "status": j.status.value,
                    **(
                        {
                            "step_timings": [
                                {"name": name, "duration": dur}
                                for name, dur in j.step_timings
                            ]
                        }
                        if j.step_timings
                        else {}
                    ),
                }
                for j in completed
            ],
            "failed_jobs": [
                {
                    "name": j.name,
                    "index": j.index,
                    "priority": j.priority,
                    "duration_seconds": j.duration_seconds,
                    "exit_code": j.exit_code,
                    "error_message": j.error_message,
                    "log_file": j.log_file,
                    "status": j.status.value,
                    **(
                        {
                            "step_timings": [
                                {"name": name, "duration": dur}
                                for name, dur in j.step_timings
                            ]
                        }
                        if j.step_timings
                        else {}
                    ),
                }
                for j in failed
            ],
            "running_jobs": [
                {
                    "name": j.name,
                    "index": j.index,
                    "priority": j.priority,
                    "elapsed_seconds": j.elapsed,
                    "status": j.status.value,
                    "current_step": j.current_step,
                }
                for j in running
            ],
            "pending_jobs": [
                {
                    "name": j.name,
                    "index": j.index,
                    "priority": j.priority,
                    "status": j.status.value,
                }
                for j in pending
            ],
            "priority_levels": {
                level.priority: {
                    "total": level.total,
                    "passed": level.passed,
                    "failed": level.failed,
                    "cancelled": level.cancelled,
                    "running": level.running,
                    "pending": level.pending,
                    "status": level.status_label,
                }
                for level in self._get_priority_levels()
            },
        }
        return result

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get_priority_levels(self) -> list[PriorityLevelProgress]:
        """Aggregate job progress by priority level."""
        levels: dict[int, PriorityLevelProgress] = {}

        with self._lock:
            for job in self._jobs.values():
                if job.priority not in levels:
                    levels[job.priority] = PriorityLevelProgress(priority=job.priority)
                level = levels[job.priority]
                level.total += 1

                if job.status == QueuedJobStatus.PASSED:
                    level.passed += 1
                elif job.status in (
                    QueuedJobStatus.FAILED,
                    QueuedJobStatus.ERROR,
                    QueuedJobStatus.TIMEOUT,
                ):
                    level.failed += 1
                elif job.status in (
                    QueuedJobStatus.CANCELLED,
                    QueuedJobStatus.SKIPPED,
                ):
                    level.cancelled += 1
                elif job.status in (
                    QueuedJobStatus.RUNNING,
                    QueuedJobStatus.PREPARING,
                ):
                    level.running += 1
                else:
                    level.pending += 1

        return sorted(levels.values(), key=lambda level: level.priority)

    def _elapsed_seconds(self) -> float:
        """Total elapsed time since first job started."""
        if self._started_at:
            return (datetime.now() - self._started_at).total_seconds()
        return 0.0

    def _elapsed_display(self) -> str:
        """Human-readable elapsed time."""
        secs = self._elapsed_seconds()
        if secs < 60:
            return f"{secs:.0f}s"
        minutes = int(secs // 60)
        remaining = secs % 60
        return f"{minutes}m {remaining:.0f}s"

    def _estimate_eta(self) -> float:
        """Estimate time remaining from completed job durations."""
        if not self._completed_durations:
            return 0.0

        with self._lock:
            total = len(self._jobs)
            completed = sum(1 for j in self._jobs.values() if j.is_terminal)
            running_count = sum(
                1
                for j in self._jobs.values()
                if j.status in (QueuedJobStatus.RUNNING, QueuedJobStatus.PREPARING)
            )
        remaining = total - completed
        if remaining <= 0:
            return 0.0

        avg_duration = sum(self._completed_durations) / len(self._completed_durations)
        effective_parallel = max(running_count, 1)
        remaining_batches = remaining / effective_parallel
        return remaining_batches * avg_duration
