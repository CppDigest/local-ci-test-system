"""Execution result aggregation and persistence.

Collects :class:`JobResult` instances from a full run, provides summary
statistics, human-readable reports, and JSON persistence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from localci.core.executor import JobResult, JobStatus


@dataclass
class ExecutionSummary:
    """Aggregated results from a full execution run."""

    execution_id: str
    started_at: datetime
    finished_at: datetime | None = None
    results: list[JobResult] = field(default_factory=list)

    # -----------------------------------------------------------------
    # Counts
    # -----------------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == JobStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == JobStatus.FAILED)

    @property
    def errors(self) -> int:
        return sum(
            1 for r in self.results if r.status in (JobStatus.ERROR, JobStatus.TIMEOUT)
        )

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == JobStatus.SKIPPED)

    @property
    def pending(self) -> int:
        return sum(
            1
            for r in self.results
            if r.status in (JobStatus.PENDING, JobStatus.PREPARING)
        )

    @property
    def running(self) -> int:
        return sum(1 for r in self.results if r.status == JobStatus.RUNNING)

    @property
    def completed(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def all_passed(self) -> bool:
        """Whether every job passed or was intentionally skipped."""
        if self.total == 0:
            return False
        return all(
            r.status in (JobStatus.PASSED, JobStatus.SKIPPED) for r in self.results
        )

    # -----------------------------------------------------------------
    # Timing
    # -----------------------------------------------------------------

    @property
    def total_duration(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return sum(r.duration_seconds for r in self.results)

    @property
    def longest_job(self) -> JobResult | None:
        if not self.results:
            return None
        return max(self.results, key=lambda r: r.duration_seconds)

    @property
    def shortest_job(self) -> JobResult | None:
        completed = [r for r in self.results if r.duration_seconds > 0]
        if not completed:
            return None
        return min(completed, key=lambda r: r.duration_seconds)

    # -----------------------------------------------------------------
    # Display
    # -----------------------------------------------------------------

    def progress_line(self) -> str:
        return f"{self.completed}/{self.total} jobs completed"

    def summary_report(self) -> str:
        lines = [
            f"{'=' * 60}",
            f"Execution Summary: {self.execution_id}",
            f"{'=' * 60}",
            "",
            f"Total:    {self.total} jobs",
            f"Passed:   {self.passed}",
            f"Failed:   {self.failed}",
            f"Errors:   {self.errors}",
            f"Skipped:  {self.skipped}",
            f"Duration: {self.total_duration:.1f}s",
            "",
        ]

        # Results table
        lines.append(f"{'Job':<45} {'Status':<10} {'Duration':>8}")
        lines.append(f"{'-' * 45} {'-' * 10} {'-' * 8}")

        for r in sorted(self.results, key=lambda x: x.matrix_index):
            lines.append(r.summary_line())

        lines.append("")

        # Failed job details
        failed = [r for r in self.results if r.status == JobStatus.FAILED]
        if failed:
            lines.append(f"{'=' * 60}")
            lines.append(f"FAILURES ({len(failed)}):")
            lines.append(f"{'=' * 60}")
            for r in failed:
                lines.append("")
                lines.append(f"--- {r.matrix_name} ---")
                if r.error_message:
                    lines.append(r.error_message)
                if r.log_file:
                    lines.append(f"Log: {r.log_file}")

        # Final verdict
        lines.append("")
        if self.all_passed:
            lines.append("RESULT: ALL PASSED ✓")
        else:
            lines.append(f"RESULT: {self.failed} FAILED, {self.errors} ERRORS ✗")

        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "execution_id": self.execution_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": (self.finished_at.isoformat() if self.finished_at else None),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "duration": self.total_duration,
            "results": [
                {
                    "job_id": r.job_id,
                    "matrix_index": r.matrix_index,
                    "matrix_name": r.matrix_name,
                    "status": r.status.value,
                    "exit_code": r.exit_code,
                    "duration": r.duration_seconds,
                    "image_used": r.image_used,
                    "log_file": str(r.log_file) if r.log_file else None,
                    "error_message": r.error_message,
                }
                for r in self.results
            ],
        }

    def save(self, path: Path) -> None:
        """Save summary to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> ExecutionSummary:
        """Load a previously-saved summary from JSON.

        Returns a minimal :class:`ExecutionSummary` with results
        populated from the JSON data.
        """
        data = json.loads(path.read_text())

        summary = cls(
            execution_id=data["execution_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            finished_at=(
                datetime.fromisoformat(data["finished_at"])
                if data.get("finished_at")
                else None
            ),
        )

        for r in data.get("results", []):
            summary.results.append(
                JobResult(
                    job_id=r["job_id"],
                    matrix_index=r["matrix_index"],
                    matrix_name=r["matrix_name"],
                    status=JobStatus(r["status"]),
                    exit_code=r.get("exit_code"),
                    duration_seconds=r.get("duration", 0.0),
                    image_used=r.get("image_used"),
                    log_file=(Path(r["log_file"]) if r.get("log_file") else None),
                    error_message=r.get("error_message"),
                )
            )

        return summary
