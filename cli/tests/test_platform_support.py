"""Tests for unsupported-platform handling (issue #79)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from localci.core.config import PlatformConfig
from localci.core.executor import JobResult, JobStatus
from localci.core.models import PlatformOutcome
from localci.core.orchestrator import OrchestratorConfig, ParallelExecutionManager
from localci.core.platform_support import (
    resolve_platform_outcome,
    skipped_platform_message,
    unsupported_platform_message,
)
from localci.core.queue import PriorityJobQueue
from localci.core.queue_builder import QueueBuilder
from localci.core.results import ExecutionSummary
from localci.core.workflow import Platform, WorkflowAnalyzer

from .test_queue import FULL_WORKFLOW as SAMPLE_WORKFLOW
from .test_queue import make_entry, make_job


class TestResolvePlatformOutcome:
    def test_linux_default_runs(self) -> None:
        entry = make_entry("GCC 15", platform=Platform.LINUX)
        assert resolve_platform_outcome(entry, PlatformConfig()) == PlatformOutcome.RUN

    def test_linux_disabled_skips(self) -> None:
        entry = make_entry("GCC 15", platform=Platform.LINUX)
        assert (
            resolve_platform_outcome(entry, PlatformConfig(linux=False))
            == PlatformOutcome.SKIP
        )

    def test_windows_default_fails(self) -> None:
        entry = make_entry("MSVC", platform=Platform.WINDOWS, compiler="msvc")
        entry.runs_on = "windows-2022"
        assert resolve_platform_outcome(entry, PlatformConfig()) == PlatformOutcome.FAIL

    def test_macos_default_fails(self) -> None:
        entry = make_entry("Apple-Clang", platform=Platform.MACOS, compiler="clang")
        entry.runs_on = "macos-26"
        assert resolve_platform_outcome(entry, PlatformConfig()) == PlatformOutcome.FAIL

    def test_windows_opt_in_skip(self) -> None:
        entry = make_entry("MSVC", platform=Platform.WINDOWS, compiler="msvc")
        entry.runs_on = "windows-latest"
        cfg = PlatformConfig(windows=True)
        assert resolve_platform_outcome(entry, cfg) == PlatformOutcome.SKIP

    def test_macos_opt_in_skip(self) -> None:
        entry = make_entry("Apple-Clang", platform=Platform.MACOS, compiler="clang")
        entry.runs_on = "macos-latest"
        cfg = PlatformConfig(macos=True)
        assert resolve_platform_outcome(entry, cfg) == PlatformOutcome.SKIP


class TestUnsupportedPlatformMessages:
    def test_failure_message_names_job_and_platform(self) -> None:
        entry = make_entry("MSVC 14.42", platform=Platform.WINDOWS, compiler="msvc")
        entry.runs_on = "windows-2022"
        msg = unsupported_platform_message("build", entry)
        assert "build" in msg
        assert "MSVC 14.42" in msg
        assert "windows" in msg
        assert "windows-2022" in msg
        assert "Linux jobs only" in msg
        assert "platforms.windows: true" in msg
        assert "--platform linux" in msg

    def test_skip_message_names_job_and_platform(self) -> None:
        entry = make_entry("Apple-Clang", platform=Platform.MACOS, compiler="clang")
        entry.runs_on = "macos-26"
        msg = skipped_platform_message("build", entry)
        assert "build" in msg
        assert "Apple-Clang" in msg
        assert "macos" in msg
        assert "Linux-only" in msg


class TestQueueBuilderPlatformOutcomes:
    def test_mixed_platform_workflow_default_config(self) -> None:
        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(SAMPLE_WORKFLOW)
        builder = QueueBuilder(workflow)
        queue = builder.build(platform_config=PlatformConfig())
        jobs = list(queue.get_all_jobs())
        assert len(jobs) == 14

        windows_jobs = [j for j in jobs if j.matrix_entry.platform == Platform.WINDOWS]
        macos_jobs = [j for j in jobs if j.matrix_entry.platform == Platform.MACOS]
        linux_jobs = [j for j in jobs if j.matrix_entry.platform == Platform.LINUX]

        assert len(windows_jobs) == 3
        assert len(macos_jobs) == 1
        assert len(linux_jobs) == 10
        assert all(j.platform_outcome == PlatformOutcome.FAIL for j in windows_jobs)
        assert all(j.platform_outcome == PlatformOutcome.FAIL for j in macos_jobs)
        assert all(j.platform_outcome == PlatformOutcome.RUN for j in linux_jobs)
        assert all(j.image_tag is not None for j in linux_jobs)
        assert all(j.image_tag is None for j in windows_jobs + macos_jobs)

    def test_mixed_platform_opt_in_skip(self) -> None:
        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(SAMPLE_WORKFLOW)
        builder = QueueBuilder(workflow)
        queue = builder.build(
            platform_config=PlatformConfig(windows=True, macos=True),
        )
        jobs = list(queue.get_all_jobs())
        non_linux = [
            j
            for j in jobs
            if j.matrix_entry.platform in (Platform.WINDOWS, Platform.MACOS)
        ]
        assert len(non_linux) == 4
        assert all(j.platform_outcome == PlatformOutcome.SKIP for j in non_linux)


class TestOrchestratorUnsupportedPlatform:
    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_windows_job_fails_without_invoking_act(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path: Path
    ) -> None:
        mock_executor = MockExecutor.return_value
        mock_docker = MockDocker.return_value
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        entry = make_entry("MSVC 14.42", platform=Platform.WINDOWS, compiler="msvc")
        entry.runs_on = "windows-2022"
        job = make_job("MSVC 14.42", compiler="msvc")
        job.matrix_entry = entry
        job.platform_outcome = PlatformOutcome.FAIL

        queue = PriorityJobQueue()
        queue.enqueue(job)

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=1),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.total == 1
        assert run.failed == 1
        assert run.all_passed is False
        result = next(iter(run.results.values()))
        assert result.status == JobStatus.FAILED
        assert "Linux jobs only" in (result.error_message or "")
        assert "windows" in (result.error_message or "")
        mock_executor.run.assert_not_called()

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_non_linux_run_outcome_still_rejected(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path: Path
    ) -> None:
        mock_executor = MockExecutor.return_value
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        entry = make_entry("MSVC 14.42", platform=Platform.WINDOWS, compiler="msvc")
        entry.runs_on = "windows-2022"
        job = make_job("MSVC 14.42", compiler="msvc")
        job.matrix_entry = entry
        job.platform_outcome = PlatformOutcome.RUN
        job.image_tag = "some-image:latest"

        queue = PriorityJobQueue()
        queue.enqueue(job)

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=1),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.failed == 1
        result = next(iter(run.results.values()))
        assert result.status == JobStatus.FAILED
        assert "Linux jobs only" in (result.error_message or "")
        mock_executor.run.assert_not_called()

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_mixed_platform_no_clean_pass_with_default_config(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path: Path
    ) -> None:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.PASSED,
            duration_seconds=1.0,
        )
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(SAMPLE_WORKFLOW)
        queue = QueueBuilder(workflow).build(platform_config=PlatformConfig())
        linux_job = next(
            j for j in queue.get_all_jobs() if j.platform_outcome == PlatformOutcome.RUN
        )
        windows_job = next(
            j
            for j in queue.get_all_jobs()
            if j.matrix_entry.platform == Platform.WINDOWS
            and j.platform_outcome == PlatformOutcome.FAIL
        )

        mixed_queue = PriorityJobQueue()
        mixed_queue.enqueue(linux_job)
        mixed_queue.enqueue(windows_job)

        orchestrator = ParallelExecutionManager(
            queue=mixed_queue,
            workflow_file=SAMPLE_WORKFLOW,
            config=OrchestratorConfig(max_parallel=2),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.failed == 1
        assert run.passed == 1
        assert run.all_passed is False
        mock_executor.run.assert_called_once()

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_opt_in_skip_allows_clean_pass(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path: Path
    ) -> None:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.PASSED,
            duration_seconds=1.0,
        )
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(SAMPLE_WORKFLOW)
        queue = QueueBuilder(workflow).build(
            platform_config=PlatformConfig(windows=True, macos=True),
        )
        linux_job = next(
            j for j in queue.get_all_jobs() if j.platform_outcome == PlatformOutcome.RUN
        )
        windows_job = next(
            j
            for j in queue.get_all_jobs()
            if j.matrix_entry.platform == Platform.WINDOWS
        )

        mixed_queue = PriorityJobQueue()
        mixed_queue.enqueue(linux_job)
        mixed_queue.enqueue(windows_job)

        orchestrator = ParallelExecutionManager(
            queue=mixed_queue,
            workflow_file=SAMPLE_WORKFLOW,
            config=OrchestratorConfig(max_parallel=2),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.failed == 0
        assert run.passed == 1
        assert run.all_passed is True
        skipped = [r for r in run.results.values() if r.status == JobStatus.SKIPPED]
        assert len(skipped) == 1
        mock_executor.run.assert_called_once()


class TestExecutionSummarySkipped:
    def test_skipped_jobs_allow_all_passed(self) -> None:
        from datetime import datetime

        summary = ExecutionSummary(
            execution_id="test",
            started_at=datetime.now(),
            results=[
                JobResult(
                    job_id="build",
                    matrix_index=0,
                    matrix_name="GCC 15",
                    status=JobStatus.PASSED,
                ),
                JobResult(
                    job_id="build",
                    matrix_index=1,
                    matrix_name="MSVC",
                    status=JobStatus.SKIPPED,
                ),
            ],
        )
        assert summary.all_passed is True

    def test_summary_report_includes_skipped_count(self) -> None:
        from datetime import datetime

        summary = ExecutionSummary(
            execution_id="test",
            started_at=datetime.now(),
            results=[
                JobResult(
                    job_id="build",
                    matrix_index=0,
                    matrix_name="GCC 15",
                    status=JobStatus.PASSED,
                ),
                JobResult(
                    job_id="build",
                    matrix_index=1,
                    matrix_name="MSVC",
                    status=JobStatus.SKIPPED,
                ),
            ],
        )
        report = summary.summary_report()
        assert "Skipped:  1" in report
        assert "ALL PASSED" in report

    def test_failed_unsupported_blocks_all_passed(self) -> None:
        from datetime import datetime

        summary = ExecutionSummary(
            execution_id="test",
            started_at=datetime.now(),
            results=[
                JobResult(
                    job_id="build",
                    matrix_index=0,
                    matrix_name="GCC 15",
                    status=JobStatus.PASSED,
                ),
                JobResult(
                    job_id="build",
                    matrix_index=1,
                    matrix_name="MSVC",
                    status=JobStatus.FAILED,
                    error_message="Linux jobs only",
                ),
            ],
        )
        assert summary.all_passed is False

    def test_progress_line_counts_skipped(self) -> None:
        from datetime import datetime

        summary = ExecutionSummary(
            execution_id="test",
            started_at=datetime.now(),
            results=[
                JobResult(
                    job_id="build",
                    matrix_index=0,
                    matrix_name="GCC 15",
                    status=JobStatus.PASSED,
                ),
                JobResult(
                    job_id="build",
                    matrix_index=1,
                    matrix_name="MSVC",
                    status=JobStatus.SKIPPED,
                ),
            ],
        )
        assert summary.completed == 2
        assert summary.progress_line() == "2/2 jobs completed"


class TestOrchestratorEmptyQueue:
    @patch("localci.core.orchestrator.DockerManager")
    def test_execute_empty_queue_returns_immediately(
        self, MockDocker, tmp_path: Path
    ) -> None:
        MockDocker.return_value.cleanup_act_containers.return_value = 0
        queue = PriorityJobQueue()
        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=1),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()
        assert run.total == 0
        assert run.all_passed is False
