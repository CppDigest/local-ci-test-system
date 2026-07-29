"""Tests for parallel execution manager (Issue 7)."""

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from localci.core.config import (
    ExecutionConfig,
    ImagesConfig,
    LocalCIConfig,
    ParallelConfig,
    ProjectConfig,
    ResourceLimitConfig,
    load_config,
)
from localci.core.executor import JobResult, JobStatus
from localci.core.models import JobEventType, QueuedJobStatus
from localci.core.orchestrator import (
    ExecutionRun,
    OrchestratorConfig,
    OrchestratorState,
    ParallelExecutionManager,
)
from localci.core.queue import PriorityJobQueue

from .test_queue import make_job

# ---------------------------------------------------------------------------
# OrchestratorConfig
# ---------------------------------------------------------------------------


class TestOrchestratorConfig:
    def test_defaults(self):
        config = OrchestratorConfig()
        assert config.max_parallel == 8
        assert config.cpu_threshold == 80.0
        assert config.memory_threshold == 70.0
        assert config.stop_on_first_failure is False

    def test_manager_uses_default_thresholds_when_config_omitted(self, tmp_path):
        with (
            patch("localci.core.orchestrator.DockerManager"),
            patch("localci.core.orchestrator.ResourceMonitor"),
        ):
            orchestrator = ParallelExecutionManager(
                queue=PriorityJobQueue(),
                workflow_file=Path("ci.yml"),
                logs_dir=tmp_path / "logs",
            )
        assert orchestrator.config.cpu_threshold == 80.0
        assert orchestrator.config.memory_threshold == 70.0

    def test_custom_config(self):
        config = OrchestratorConfig(
            max_parallel=20,
            job_timeout=1800,
            stop_on_first_failure=True,
        )
        assert config.max_parallel == 20
        assert config.job_timeout == 1800


class TestOrchestratorConfigFromConfig:
    def test_from_config_default_localci_config(self):
        cfg = LocalCIConfig()
        orch = OrchestratorConfig.from_config(cfg)
        assert orch.max_parallel == 8
        assert orch.cpu_threshold == 80.0
        assert orch.memory_threshold == 70.0
        assert orch.disk_min_free_gb == 10.0
        assert orch.job_timeout == 3600
        assert orch.keep_containers is False
        assert orch.stop_on_first_failure is False
        assert orch.repo_full_name == ""
        assert orch.native_image_prefix == ""
        assert orch.auto_build is True

    def test_from_config_maps_explicit_values(self, tmp_path):
        registry = tmp_path / "registry.yml"
        cfg = LocalCIConfig(
            parallel=ParallelConfig(
                max_jobs=16,
                resource_limit=ResourceLimitConfig(cpu_percent=90, memory_percent=80),
            ),
            execution=ExecutionConfig(
                timeout=7200,
                keep_containers=True,
                stop_on_first_failure=True,
            ),
            project=ProjectConfig(
                repo_full_name="org/repo",
                native_image_prefix="custom-",
            ),
            images=ImagesConfig(registry=registry, auto_build=False),
        )
        orch = OrchestratorConfig.from_config(cfg)
        assert orch.max_parallel == 16
        assert orch.cpu_threshold == 90.0
        assert orch.memory_threshold == 80.0
        assert orch.job_timeout == 7200
        assert orch.keep_containers is True
        assert orch.stop_on_first_failure is True
        assert orch.repo_full_name == "org/repo"
        assert orch.native_image_prefix == "custom-"
        assert orch.image_registry_path == registry.resolve()
        assert orch.auto_build is False

    def test_from_config_round_trips_yaml_file(self, tmp_path):
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text(
            "parallel:\n"
            "  max_jobs: 12\n"
            "  resource_limit:\n"
            "    cpu_percent: 75\n"
            "    memory_percent: 65\n"
            "execution:\n"
            "  timeout: 1800\n"
            "  keep_containers: true\n"
        )
        orch = OrchestratorConfig.from_config(load_config(cfg_file))
        assert orch.max_parallel == 12
        assert orch.cpu_threshold == 75.0
        assert orch.memory_threshold == 65.0
        assert orch.job_timeout == 1800
        assert orch.keep_containers is True
        assert orch.disk_min_free_gb == 10.0


# ---------------------------------------------------------------------------
# ExecutionRun
# ---------------------------------------------------------------------------


class TestExecutionRun:
    def test_passed_count(self):
        from datetime import datetime

        run = ExecutionRun(
            execution_id="test",
            started_at=datetime.now(),
        )
        run.results["a"] = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="A",
            status=JobStatus.PASSED,
        )
        run.results["b"] = JobResult(
            job_id="build",
            matrix_index=1,
            matrix_name="B",
            status=JobStatus.FAILED,
        )
        assert run.total == 2
        assert run.passed == 1
        assert run.failed == 1
        assert run.all_passed is False

    def test_all_passed(self):
        from datetime import datetime

        run = ExecutionRun(
            execution_id="test",
            started_at=datetime.now(),
        )
        run.results["a"] = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="A",
            status=JobStatus.PASSED,
        )
        assert run.all_passed is True


# ---------------------------------------------------------------------------
# ParallelExecutionManager (mocked)
# ---------------------------------------------------------------------------


class TestParallelExecutionManager:
    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_execute_single_job(self, MockExecutor, MockDocker, MockMonitor, tmp_path):
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.PASSED,
            duration_seconds=10.0,
        )
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        queue = PriorityJobQueue()
        queue.enqueue(make_job("GCC 15", priority=1))

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=4),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.total == 1
        assert run.passed == 1
        assert run.all_passed is True
        mock_docker.cleanup_act_containers.assert_called_once()
        session_arg = mock_docker.cleanup_act_containers.call_args[0][0]
        assert session_arg == run.execution_id

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_parallel_execution(self, MockExecutor, MockDocker, MockMonitor, tmp_path):
        def mock_run(*_args, **_kwargs):
            time.sleep(0.1)
            return JobResult(
                job_id="build",
                matrix_index=0,
                matrix_name="test",
                status=JobStatus.PASSED,
                duration_seconds=0.1,
            )

        mock_executor = MockExecutor.return_value
        mock_executor.run.side_effect = mock_run
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        queue = PriorityJobQueue()
        for i in range(4):
            queue.enqueue(make_job(f"Job {i}", priority=1, index=i))

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=4),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.total == 4
        assert run.passed == 4
        assert run.duration_seconds < 0.5

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_stop_on_first_failure(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path
    ):
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="fail",
            status=JobStatus.FAILED,
            duration_seconds=1.0,
        )
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        queue = PriorityJobQueue()
        for i in range(5):
            queue.enqueue(make_job(f"Job {i}", priority=1, index=i))

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(
                max_parallel=1,
                stop_on_first_failure=True,
            ),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.failed >= 1
        assert run.total < 5

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_priority_enforcement(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path
    ):
        execution_order = []

        def mock_run(_cmd, matrix_index=0, matrix_name="", **_kwargs):
            execution_order.append(matrix_name)
            time.sleep(0.05)
            return JobResult(
                job_id="build",
                matrix_index=matrix_index,
                matrix_name=matrix_name,
                status=JobStatus.PASSED,
                duration_seconds=0.05,
            )

        mock_executor = MockExecutor.return_value
        mock_executor.run.side_effect = mock_run
        mock_docker = MockDocker.return_value
        mock_docker.image_exists.return_value = True
        mock_docker.cleanup_act_containers.return_value = 0
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "OK")

        queue = PriorityJobQueue()
        queue.enqueue(make_job("P1-A", priority=1, index=0))
        queue.enqueue(make_job("P2-A", priority=2, index=1))

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=4),
            logs_dir=tmp_path / "logs",
        )
        run = orchestrator.execute()

        assert run.total == 2
        assert run.passed == 2
        assert execution_order.index("P1-A") < execution_order.index("P2-A")

    def test_event_emission(self):
        events = []
        queue = PriorityJobQueue()
        queue.add_listener(lambda e: events.append(e))
        job = make_job("Test", priority=1)
        queue.enqueue(job)
        assert any(e.event_type == JobEventType.JOB_QUEUED for e in events)

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_get_status(self, MockExecutor, MockDocker, MockMonitor, tmp_path):
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
        mock_monitor.snapshot.return_value = MagicMock(summary=lambda: "CPU: 10%")

        queue = PriorityJobQueue()
        queue.enqueue(make_job("GCC 15", priority=1))
        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=4),
            logs_dir=tmp_path / "logs",
        )
        assert orchestrator.get_status()["state"] == "idle"
        orchestrator.execute()
        status = orchestrator.get_status()
        assert status["state"] in ("completed", "running")
        assert "total_jobs" in status
        assert "resources" in status

    @patch("localci.core.orchestrator.ResourceMonitor")
    @patch("localci.core.orchestrator.DockerManager")
    @patch("localci.core.orchestrator.JobExecutor")
    def test_dispatch_pool_none_marks_job_failed(
        self, MockExecutor, MockDocker, MockMonitor, tmp_path
    ):
        mock_monitor = MockMonitor.return_value
        mock_monitor.check_thresholds.return_value = (True, [])

        queue = PriorityJobQueue()
        job = make_job("GCC 15", priority=1)
        queue.enqueue(job)

        orchestrator = ParallelExecutionManager(
            queue=queue,
            workflow_file=Path("ci.yml"),
            config=OrchestratorConfig(max_parallel=4),
            logs_dir=tmp_path / "logs",
        )
        orchestrator._run = ExecutionRun(
            execution_id="test",
            started_at=datetime.now(),
            state=OrchestratorState.RUNNING,
        )
        orchestrator._state = OrchestratorState.RUNNING
        orchestrator._pool = None

        orchestrator._dispatch_loop()

        result = orchestrator._run.results[job.queue_key]
        assert result.status == JobStatus.ERROR
        assert result.error_message == "Orchestrator thread pool unavailable"
        failed_jobs = queue.get_jobs_by_status(QueuedJobStatus.FAILED)
        assert len(failed_jobs) == 1
        assert failed_jobs[0].queue_key == job.queue_key
        MockExecutor.return_value.run.assert_not_called()
