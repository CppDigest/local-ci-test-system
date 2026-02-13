"""Tests for real-time progress tracking (Issue 8)."""

import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from localci.core.executor import JobResult, JobStatus
from localci.core.models import (
    JobEvent,
    JobEventType,
    QueuedJob,
    QueuedJobStatus,
)
from localci.core.progress import (
    JobProgress,
    PriorityLevelProgress,
    ProgressTracker,
)
from localci.core.queue import PriorityJobQueue

from tests.test_queue import make_job


# ---------------------------------------------------------------------------
# JobProgress tests
# ---------------------------------------------------------------------------


class TestJobProgress:
    def test_status_icons(self):
        jp = JobProgress(key="a", name="test", index=0, priority=1)

        jp.status = QueuedJobStatus.QUEUED
        assert jp.status_icon == "◌"

        jp.status = QueuedJobStatus.RUNNING
        assert jp.status_icon == "●"

        jp.status = QueuedJobStatus.PASSED
        assert jp.status_icon == "✓"

        jp.status = QueuedJobStatus.FAILED
        assert jp.status_icon == "✗"

    def test_elapsed_not_started(self):
        jp = JobProgress(key="a", name="test", index=0, priority=1)
        assert jp.elapsed == 0.0
        assert jp.elapsed_display == "-"

    def test_elapsed_running(self):
        jp = JobProgress(
            key="a",
            name="test",
            index=0,
            priority=1,
            status=QueuedJobStatus.RUNNING,
            started_at=datetime.now(),
        )
        time.sleep(0.1)
        assert jp.elapsed > 0

    def test_elapsed_finished(self):
        jp = JobProgress(
            key="a",
            name="test",
            index=0,
            priority=1,
            status=QueuedJobStatus.PASSED,
            duration_seconds=45.0,
            finished_at=datetime.now(),
        )
        assert jp.elapsed == 45.0
        assert jp.elapsed_display == "45s"

    def test_elapsed_display_minutes(self):
        jp = JobProgress(
            key="a",
            name="test",
            index=0,
            priority=1,
            status=QueuedJobStatus.PASSED,
            duration_seconds=125.0,
            finished_at=datetime.now(),
        )
        assert "2m" in jp.elapsed_display

    def test_is_terminal(self):
        jp = JobProgress(key="a", name="test", index=0, priority=1)

        jp.status = QueuedJobStatus.RUNNING
        assert jp.is_terminal is False

        jp.status = QueuedJobStatus.PASSED
        assert jp.is_terminal is True

        jp.status = QueuedJobStatus.FAILED
        assert jp.is_terminal is True


# ---------------------------------------------------------------------------
# PriorityLevelProgress tests
# ---------------------------------------------------------------------------


class TestPriorityLevelProgress:
    def test_complete(self):
        level = PriorityLevelProgress(
            priority=1, total=3, passed=2, failed=1
        )
        assert level.complete == 3
        assert level.is_done is True

    def test_in_progress(self):
        level = PriorityLevelProgress(
            priority=1, total=3, passed=1, running=1, pending=1
        )
        assert level.complete == 1
        assert level.is_done is False
        assert level.status_icon == "●"

    def test_all_passed(self):
        level = PriorityLevelProgress(
            priority=1, total=2, passed=2
        )
        assert level.is_done is True
        assert level.status_icon == "✓"

    def test_has_failures(self):
        level = PriorityLevelProgress(
            priority=1, total=2, passed=1, failed=1
        )
        assert level.status_icon == "✗"


# ---------------------------------------------------------------------------
# ProgressTracker tests
# ---------------------------------------------------------------------------


class TestProgressTracker:
    @pytest.fixture
    def queue(self):
        return PriorityJobQueue()

    @pytest.fixture
    def tracker(self, queue):
        return ProgressTracker(
            queue=queue,
            workflow_file="ci.yml",
            platform="linux",
            max_parallel=8,
        )

    def test_track_job_queued(self, tracker):
        job = make_job("GCC 15", priority=1)
        event = JobEvent(
            event_type=JobEventType.JOB_QUEUED,
            job=job,
        )

        tracker.on_event(event)

        assert len(tracker._jobs) == 1
        assert tracker._jobs[job.queue_key].status == QueuedJobStatus.QUEUED

    def test_track_job_lifecycle(self, tracker):
        job = make_job("GCC 15", priority=1)

        tracker.on_event(
            JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
        )
        assert tracker._jobs[job.queue_key].status == QueuedJobStatus.QUEUED

        tracker.on_event(
            JobEvent(event_type=JobEventType.JOB_PREPARING, job=job)
        )
        assert tracker._jobs[job.queue_key].status == QueuedJobStatus.PREPARING

        tracker.on_event(
            JobEvent(event_type=JobEventType.JOB_STARTED, job=job)
        )
        assert tracker._jobs[job.queue_key].status == QueuedJobStatus.RUNNING
        assert tracker._jobs[job.queue_key].started_at is not None

        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.PASSED,
            duration_seconds=30.0,
            exit_code=0,
        )
        tracker.on_event(
            JobEvent(
                event_type=JobEventType.JOB_COMPLETED,
                job=job,
                data={"result": result},
            )
        )
        assert tracker._jobs[job.queue_key].status == QueuedJobStatus.PASSED
        assert tracker._jobs[job.queue_key].duration_seconds == 30.0

    def test_track_failure(self, tracker):
        job = make_job("Fail", priority=1)

        tracker.on_event(
            JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
        )

        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="Fail",
            status=JobStatus.FAILED,
            duration_seconds=10.0,
            exit_code=1,
            error_message="compilation failed",
        )
        tracker.on_event(
            JobEvent(
                event_type=JobEventType.JOB_FAILED,
                job=job,
                data={"result": result},
            )
        )

        progress = tracker._jobs[job.queue_key]
        assert progress.status == QueuedJobStatus.FAILED
        assert progress.error_message == "compilation failed"

    def test_track_timeout(self, tracker):
        job = make_job("Timeout", priority=1)
        tracker.on_event(
            JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
        )
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="Timeout",
            status=JobStatus.TIMEOUT,
            duration_seconds=3600.0,
            error_message="Timed out",
        )
        tracker.on_event(
            JobEvent(
                event_type=JobEventType.JOB_TIMEOUT,
                job=job,
                data={"result": result},
            )
        )
        progress = tracker._jobs[job.queue_key]
        assert progress.status == QueuedJobStatus.TIMEOUT
        assert progress.error_message == "Timed out"

    def test_get_status_dict(self, tracker):
        for i, (name, status_event) in enumerate([
            ("Passed", JobEventType.JOB_COMPLETED),
            ("Failed", JobEventType.JOB_FAILED),
            ("Running", JobEventType.JOB_STARTED),
        ]):
            job = make_job(name, priority=1, index=i)
            tracker.on_event(
                JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
            )

            if status_event == JobEventType.JOB_STARTED:
                tracker.on_event(
                    JobEvent(event_type=status_event, job=job)
                )
            elif status_event == JobEventType.JOB_COMPLETED:
                result = JobResult(
                    job_id="build",
                    matrix_index=i,
                    matrix_name=name,
                    status=JobStatus.PASSED,
                    duration_seconds=10.0,
                )
                tracker.on_event(
                    JobEvent(
                        event_type=status_event,
                        job=job,
                        data={"result": result},
                    )
                )
            elif status_event == JobEventType.JOB_FAILED:
                result = JobResult(
                    job_id="build",
                    matrix_index=i,
                    matrix_name=name,
                    status=JobStatus.FAILED,
                    duration_seconds=5.0,
                    error_message="error",
                )
                tracker.on_event(
                    JobEvent(
                        event_type=status_event,
                        job=job,
                        data={"result": result},
                    )
                )

        status = tracker.get_status_dict()

        assert status["total"] == 3
        assert status["passed_count"] == 1
        assert status["failed_count"] == 1
        assert status["running_count"] == 1
        assert len(status["completed_jobs"]) == 1
        assert len(status["failed_jobs"]) == 1
        assert len(status["running_jobs"]) == 1
        assert status["failed_jobs"][0]["error_message"] == "error"

    def test_priority_levels(self, tracker):
        for i, (name, priority) in enumerate([
            ("P1-A", 1),
            ("P1-B", 1),
            ("P2-A", 2),
        ]):
            job = make_job(name, priority=priority, index=i)
            tracker.on_event(
                JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
            )

        levels = tracker._get_priority_levels()
        assert len(levels) == 2
        assert levels[0].priority == 1
        assert levels[0].total == 2
        assert levels[1].priority == 2
        assert levels[1].total == 1

    def test_eta_estimation(self, tracker):
        for i in range(3):
            job = make_job(f"Job {i}", priority=1, index=i)
            tracker.on_event(
                JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
            )
            result = JobResult(
                job_id="build",
                matrix_index=i,
                matrix_name=f"Job {i}",
                status=JobStatus.PASSED,
                duration_seconds=30.0,
            )
            tracker.on_event(
                JobEvent(
                    event_type=JobEventType.JOB_COMPLETED,
                    job=job,
                    data={"result": result},
                )
            )
        for i in range(3, 6):
            job = make_job(f"Job {i}", priority=2, index=i)
            tracker.on_event(
                JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
            )

        eta = tracker._estimate_eta()
        assert eta > 0

    def test_thread_safety(self, tracker):
        def emit_events(start, count):
            for i in range(start, start + count):
                job = make_job(f"Job {i}", priority=1, index=i)
                tracker.on_event(
                    JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
                )

        threads = [
            threading.Thread(target=emit_events, args=(0, 50)),
            threading.Thread(target=emit_events, args=(50, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(tracker._jobs) == 100

    def test_set_execution_id(self, tracker):
        tracker.set_execution_id("abc123")
        assert tracker._execution_id == "abc123"
        d = tracker.get_status_dict()
        assert d["execution_id"] == "abc123"

    def test_execution_id_from_event(self, tracker):
        job = make_job("J", priority=1)
        tracker.on_event(
            JobEvent(
                event_type=JobEventType.JOB_QUEUED,
                job=job,
                data={"execution_id": "xyz789"},
            )
        )
        assert tracker._execution_id == "xyz789"
