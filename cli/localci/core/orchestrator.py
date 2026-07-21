"""Parallel execution manager: orchestrate CI jobs from the priority queue."""

from __future__ import annotations

import logging
import shlex
import signal
import subprocess
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Event as ThreadEvent
from typing import TYPE_CHECKING, Any

from localci.core.cmake_cache import compute_cmake_input_digest
from localci.core.command_builder import ActCommandBuilder
from localci.core.config import (
    GENERIC_NATIVE_IMAGE_PREFIX,
    GENERIC_REPO_FULL_NAME,
    resolve_cache_paths,
)
from localci.core.executor import JobExecutor, JobResult, JobStatus
from localci.core.models import JobEvent, JobEventType, QueuedJob
from localci.core.queue import PriorityJobQueue
from localci.utils.docker import DockerManager
from localci.utils.resources import ResourceMonitor

if TYPE_CHECKING:
    from localci.core.config import CacheConfig, LocalCIConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and config
# ---------------------------------------------------------------------------


class OrchestratorState(Enum):
    """State of the orchestrator."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class OrchestratorConfig:
    """Configuration for the parallel execution manager."""

    max_parallel: int = 8
    cpu_threshold: float = 90.0
    memory_threshold: float = 85.0
    disk_min_free_gb: float = 10.0
    job_timeout: int = 3600
    keep_containers: bool = False
    stop_on_first_failure: bool = False
    resource_check_interval: float = 5.0
    dispatch_interval: float = 0.1
    default_secrets: dict[str, str] | None = None
    default_env: dict[str, str] | None = None
    repo_full_name: str = GENERIC_REPO_FULL_NAME
    native_image_prefix: str = GENERIC_NATIVE_IMAGE_PREFIX
    image_registry_path: Path | None = None
    verbose: bool = False
    offline: bool = False
    auto_build: bool = True

    @classmethod
    def from_config(cls, config: LocalCIConfig) -> OrchestratorConfig:
        rl = getattr(config.parallel, "resource_limit", None) or {}
        cpu = getattr(rl, "cpu_percent", 90) if hasattr(rl, "cpu_percent") else 90.0
        mem = (
            getattr(rl, "memory_percent", 85) if hasattr(rl, "memory_percent") else 85.0
        )
        disk_gb = float(getattr(rl, "disk_min_free_gb", 10.0))
        images = getattr(config, "images", None)
        registry_path = getattr(images, "registry", None) if images else None
        project = getattr(config, "project", None)
        return cls(
            max_parallel=getattr(config.parallel, "max_jobs", 8),
            cpu_threshold=float(cpu),
            memory_threshold=float(mem),
            disk_min_free_gb=disk_gb,
            job_timeout=getattr(config.execution, "timeout", 3600),
            keep_containers=getattr(config.execution, "keep_containers", False),
            stop_on_first_failure=getattr(
                config.execution, "stop_on_first_failure", False
            ),
            repo_full_name=getattr(project, "repo_full_name", GENERIC_REPO_FULL_NAME),
            native_image_prefix=getattr(
                project, "native_image_prefix", GENERIC_NATIVE_IMAGE_PREFIX
            ),
            image_registry_path=registry_path,
            auto_build=getattr(images, "auto_build", True) if images else True,
        )


@dataclass
class ExecutionRun:
    """Complete record of an orchestrator run."""

    execution_id: str
    started_at: datetime
    finished_at: datetime | None = None
    state: OrchestratorState = OrchestratorState.IDLE
    results: dict[str, JobResult] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results.values() if r.status == JobStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(
            1
            for r in self.results.values()
            if r.status in (JobStatus.FAILED, JobStatus.ERROR, JobStatus.TIMEOUT)
        )

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.total > 0


# ---------------------------------------------------------------------------
# Parallel execution manager
# ---------------------------------------------------------------------------


class ParallelExecutionManager:
    """Orchestrate parallel execution of CI jobs from the priority queue."""

    def __init__(
        self,
        queue: PriorityJobQueue,
        workflow_file: Path,
        project_dir: Path = Path("."),
        config: OrchestratorConfig | None = None,
        logs_dir: Path | None = None,
        workflow_patcher: Callable[..., Path]
        | None = None,  # (workflow_path, entry, image_tag, job_id=..., container_mount_options=...) -> Path
        cache_config: CacheConfig | None = None,
        no_cache: bool = False,
        cache_dir_override: Path | None = None,
    ):
        self.queue = queue
        self.workflow_file = Path(workflow_file)
        self.project_dir = Path(project_dir).resolve()
        self.config = config or OrchestratorConfig()
        self.logs_dir = Path(logs_dir or Path.home() / ".localci" / "logs").expanduser()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._workflow_patcher = workflow_patcher
        self._cache_config = cache_config
        self._no_cache = no_cache
        self._cache_dir_override = (
            Path(cache_dir_override).expanduser().resolve()
            if cache_dir_override is not None
            else None
        )

        self._executor = JobExecutor(logs_dir=self.logs_dir)
        self._docker = DockerManager()
        self._resource_monitor = ResourceMonitor()

        self._state = OrchestratorState.IDLE
        self._run: ExecutionRun | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[JobResult]] = {}
        self._shutdown_event = ThreadEvent()
        self._listeners: list[Callable[[JobEvent], None]] = []

        self.queue.add_listener(self._on_queue_event)

    def add_listener(self, callback: Callable[[JobEvent], None]) -> None:
        self._listeners.append(callback)

    def _emit(self, event_type: JobEventType, job: QueuedJob, **data: object) -> None:
        payload = dict(data)
        if self._run:
            payload["execution_id"] = self._run.execution_id
        event = JobEvent(event_type=event_type, job=job, data=payload)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning("Listener error: %s", e)

    def _on_queue_event(self, event: JobEvent) -> None:
        payload = dict(event.data)
        if self._run:
            payload["execution_id"] = self._run.execution_id
        forwarded = JobEvent(
            event_type=event.event_type,
            job=event.job,
            data=payload,
            timestamp=event.timestamp,
        )
        for listener in self._listeners:
            try:
                listener(forwarded)
            except Exception as e:
                logger.warning("Listener error: %s", e)

    def execute(self) -> ExecutionRun:
        self._run = ExecutionRun(
            execution_id=str(uuid.uuid4())[:8],
            started_at=datetime.now(),
            state=OrchestratorState.RUNNING,
        )
        self._state = OrchestratorState.RUNNING
        self._shutdown_event.clear()

        logger.info(
            "Starting execution %s: %d jobs, max_parallel=%d",
            self._run.execution_id,
            self.queue.total_jobs,
            self.config.max_parallel,
        )

        try:
            sig = getattr(signal, "SIGINT", None)
            original_sigint = signal.signal(sig, self._handle_sigint) if sig else None
        except (ValueError, AttributeError):
            original_sigint = None

        try:
            self._pool = ThreadPoolExecutor(
                max_workers=self.config.max_parallel,
                thread_name_prefix="localci-worker",
            )
            self._dispatch_loop()
            self._wait_for_completion()
        except Exception as e:
            logger.exception("Orchestrator error: %s", e)
            self._state = OrchestratorState.FAILED
        finally:
            if original_sigint is not None and hasattr(signal, "SIGINT"):
                with suppress(ValueError, AttributeError):
                    signal.signal(signal.SIGINT, original_sigint)
            if self._pool:
                self._pool.shutdown(wait=True)
            if not self.config.keep_containers:
                self._cleanup_all_containers()
            if self._run:
                self._run.finished_at = datetime.now()
                if self._state == OrchestratorState.FAILED:
                    self._run.state = OrchestratorState.FAILED
                elif self._state == OrchestratorState.CANCELLING:
                    self._run.state = OrchestratorState.CANCELLED
                else:
                    self._run.state = OrchestratorState.COMPLETED

        logger.info(
            "Execution %s complete: %d/%d passed in %.1fs",
            self._run.execution_id,
            self._run.passed,
            self._run.total,
            self._run.duration_seconds,
        )
        return self._run

    def _dispatch_loop(self) -> None:
        last_resource_check = 0.0
        while not self.queue.is_done and not self._shutdown_event.is_set():
            now = time.time()
            if now - last_resource_check > self.config.resource_check_interval:
                self._check_resources()
                last_resource_check = now
            if self._state == OrchestratorState.PAUSED:
                time.sleep(self.config.dispatch_interval)
                continue
            active_count = len([f for f in self._futures.values() if not f.done()])
            if active_count >= self.config.max_parallel:
                time.sleep(self.config.dispatch_interval)
                continue
            if self.config.stop_on_first_failure and self.queue.failed_count > 0:
                logger.warning("Stopping: first failure detected")
                self.queue.cancel_all()
                break
            job = self.queue.next_ready()
            if job is None:
                time.sleep(self.config.dispatch_interval)
                continue
            logger.info("Dispatching: %s", job.matrix_entry.name)
            pool = self._pool
            if pool is None:
                logger.error(
                    "Thread pool unavailable; marking job as failed: %s",
                    job.matrix_entry.name,
                )
                failed = Future[JobResult]()
                failed.set_result(
                    JobResult(
                        job_id=job.job_id,
                        matrix_index=job.matrix_entry.index,
                        matrix_name=job.matrix_entry.name,
                        status=JobStatus.ERROR,
                        error_message="Orchestrator thread pool unavailable",
                    )
                )
                self._futures[job.queue_key] = failed
                self._on_job_done(job, failed)
                continue
            future = pool.submit(self._execute_job, job)
            self._futures[job.queue_key] = future
            future.add_done_callback(self._make_done_callback(job))

    def _wait_for_completion(self) -> None:
        for key, future in list(self._futures.items()):
            if not future.done():
                try:
                    future.result(timeout=self.config.job_timeout + 60)
                except Exception as e:
                    logger.error("Future error for %s: %s", key, e)

    def _execute_job(self, job: QueuedJob) -> JobResult:
        try:
            self.queue.mark_preparing(job)
            image_tag = self._prepare_image(job)
            if job.image_tag and image_tag is None:
                return JobResult(
                    job_id=job.job_id,
                    matrix_index=job.matrix_entry.index,
                    matrix_name=job.matrix_entry.name,
                    status=JobStatus.ERROR,
                    error_message=f"Image not available: {job.image_tag} (not in Docker cache and load from .tar failed or not attempted)",
                )
            self.queue.mark_running(job)
            # Phase 2: resolve cache paths before patcher (patcher may inject mounts into workflow)
            resolved_cache_paths = None
            container_mount_options: str | None = None
            if self._cache_config is not None:
                cmake_digest = None
                if (
                    not self._no_cache
                    and self._cache_config.enabled
                    and self._cache_config.cmake.enabled
                ):
                    cmake_digest = compute_cmake_input_digest(
                        self.project_dir,
                        job.matrix_entry,
                        self._cache_config.cmake,
                        boost_enabled=(self._cache_config.boost.enabled),
                    )
                resolved_cache_paths = resolve_cache_paths(
                    self._cache_config,
                    self._no_cache,
                    self._cache_dir_override,
                    job.job_id,
                    job.queue_key,
                    cmake_input_digest=cmake_digest,
                )
            if resolved_cache_paths is not None:
                for d in resolved_cache_paths.host_dirs_to_ensure():
                    d.mkdir(parents=True, exist_ok=True)
                # Build -v options so patcher can inject into job container (act does not apply --container-options to job container when workflow has container:)
                mount_parts: list[str] = []
                if resolved_cache_paths.ccache_host is not None:
                    mount_parts.append(
                        f"-v {shlex.quote(str(resolved_cache_paths.ccache_host))}:{shlex.quote(str(resolved_cache_paths.ccache_container))}"
                    )
                if resolved_cache_paths.boost_host is not None:
                    mount_parts.append(
                        f"-v {shlex.quote(str(resolved_cache_paths.boost_host))}:{shlex.quote(str(resolved_cache_paths.boost_container))}"
                    )
                if resolved_cache_paths.cmake_host is not None:
                    mount_parts.append(
                        f"-v {shlex.quote(str(resolved_cache_paths.cmake_host))}:{shlex.quote(str(resolved_cache_paths.cmake_container))}"
                    )
                if resolved_cache_paths.b2_source_host is not None:
                    mount_parts.append(
                        f"-v {shlex.quote(str(resolved_cache_paths.b2_source_host))}:{shlex.quote(str(resolved_cache_paths.b2_source_container))}"
                    )
                if resolved_cache_paths.apt_host is not None:
                    mount_parts.append(
                        f"-v {shlex.quote(str(resolved_cache_paths.apt_host))}:{shlex.quote(str(resolved_cache_paths.apt_container))}"
                    )
                if mount_parts:
                    container_mount_options = " ".join(mount_parts)
                    parts = []
                    if resolved_cache_paths.ccache_host is not None:
                        parts.append("ccache")
                    if resolved_cache_paths.boost_host is not None:
                        parts.append("boost")
                    if resolved_cache_paths.cmake_host is not None:
                        parts.append("cmake")
                    if resolved_cache_paths.b2_source_host is not None:
                        parts.append("b2-source")
                    if resolved_cache_paths.apt_host is not None:
                        parts.append("apt")
                    logger.info(
                        "Cache mounts applied for %s: %s",
                        job.matrix_entry.name,
                        ", ".join(parts),
                    )
            elif (
                self._cache_config is not None
                and not self._no_cache
                and self._cache_config.enabled
            ):
                logger.debug(
                    "Cache enabled in config but no paths resolved for %s (check job_id/queue_key and cache sub-options)",
                    job.matrix_entry.name,
                )

            workflow_file = self.workflow_file
            if self._workflow_patcher is not None:
                workflow_file = self._workflow_patcher(
                    self.workflow_file,
                    job.matrix_entry,
                    image_tag,
                    job_id=job.job_id,
                    container_mount_options=container_mount_options,
                )

            try:
                # Per-job act action cache to avoid parallel jobs sharing ~/.cache/act
                # (causes "remove ... no such file or directory" when one job cleans cache)
                act_cache_dir = (
                    self.logs_dir / "act-cache" / job.queue_key.replace(":", "-")
                )
                act_cache_dir.mkdir(parents=True, exist_ok=True)

                builder = ActCommandBuilder(
                    workflow_file=self.workflow_file,
                    project_dir=self.project_dir,
                    job_id=job.job_id,
                    repo_full_name=self.config.repo_full_name,
                    native_image_prefix=self.config.native_image_prefix,
                    default_secrets=self.config.default_secrets or {},
                    default_env=self.config.default_env or {},
                    offline=self.config.offline,
                    act_version=self._executor.act_version_tuple,
                )
                cmd = builder.build(
                    job.matrix_entry,
                    image_tag=image_tag,
                    verbose=self.config.verbose,
                    workflow_file=workflow_file,
                    action_cache_path=act_cache_dir,
                    resolved_cache_paths=resolved_cache_paths,
                    cache_config=self._cache_config,
                )
                result = self._executor.run(
                    cmd,
                    matrix_index=job.matrix_entry.index,
                    matrix_name=job.matrix_entry.name,
                    timeout=self.config.job_timeout,
                    stream_output=False,
                    on_output=lambda line: self._emit(
                        JobEventType.JOB_OUTPUT, job, line=line
                    ),
                )
                return result
            finally:
                if (
                    self._workflow_patcher is not None
                    and workflow_file != self.workflow_file
                ):
                    try:
                        workflow_file.unlink(missing_ok=True)
                    except OSError as unlink_err:
                        logger.debug(
                            "Could not remove patched workflow temp file %s: %s",
                            workflow_file,
                            unlink_err,
                        )
        except Exception as e:
            logger.exception(
                "Job execution error: %s: %s",
                job.matrix_entry.name,
                e,
            )
            return JobResult(
                job_id=job.job_id,
                matrix_index=job.matrix_entry.index,
                matrix_name=job.matrix_entry.name,
                status=JobStatus.ERROR,
                error_message=str(e),
            )

    def _prepare_image(self, job: QueuedJob) -> str | None:
        if not job.image_tag:
            logger.warning("No image tag for %s", job.matrix_entry.name)
            return None
        if self._docker.image_exists(job.image_tag):
            logger.debug("Image already loaded: %s", job.image_tag)
            return job.image_tag
        # Load from .tar if not in Docker cache (design: "Load image from tar file" step 1)
        registry = self.config.image_registry_path
        if registry and registry.is_dir():
            # Convention: image tag "name:latest" -> registry/name.tar
            base = job.image_tag.split(":")[0]
            tar_path = registry / f"{base}.tar"
            if tar_path.exists():
                ok, msg = self._docker.load_image(tar_path)
                if ok and self._docker.image_exists(job.image_tag):
                    logger.info("Loaded image from %s: %s", tar_path, job.image_tag)
                    return job.image_tag
                if not ok:
                    logger.warning("Failed to load image from %s: %s", tar_path, msg)
            else:
                logger.debug("No tar at %s for %s", tar_path, job.image_tag)
        else:
            logger.debug(
                "No image registry path; image must be pre-loaded: %s", job.image_tag
            )
        # If still missing and job.needs_build, build synchronously (Design Guide: "jobs are never skipped due to missing images")
        if (
            not self._docker.image_exists(job.image_tag)
            and job.needs_build
            and self.config.auto_build
        ):
            build_script = self.project_dir / "images" / "capy" / "build-one.sh"
            if build_script.exists():
                image_name = job.image_tag.split(":")[0]
                try:
                    result = subprocess.run(
                        ["bash", str(build_script), image_name, "--save"],
                        cwd=str(self.project_dir),
                        capture_output=True,
                        text=True,
                        timeout=3600,
                    )
                    if result.returncode == 0 and self._docker.image_exists(
                        job.image_tag
                    ):
                        logger.info(
                            "Built image for %s: %s",
                            job.matrix_entry.name,
                            job.image_tag,
                        )
                        return job.image_tag
                    if result.returncode != 0:
                        logger.warning(
                            "Build failed for %s (exit %s): %s",
                            job.image_tag,
                            result.returncode,
                            (result.stderr or result.stdout or "").strip()
                            or "(no output)",
                        )
                except subprocess.TimeoutExpired:
                    logger.warning("Build timed out for %s", job.image_tag)
                except (OSError, FileNotFoundError) as e:
                    logger.warning(
                        "Could not run build script for %s: %s", job.image_tag, e
                    )
            else:
                logger.debug(
                    "No build script at %s; cannot build %s",
                    build_script,
                    job.image_tag,
                )

        # Only return tag if image is now present (e.g. after load or build); else None so job fails clearly
        if self._docker.image_exists(job.image_tag):
            return job.image_tag
        logger.warning(
            "Image not available (not in cache, load from tar failed or not attempted, and build skipped or failed): %s",
            job.image_tag,
        )
        return None

    def _make_done_callback(
        self, job: QueuedJob
    ) -> Callable[[Future[JobResult]], None]:
        def callback(future: Future[JobResult]) -> None:
            self._on_job_done(job, future)

        return callback

    def _on_job_done(self, job: QueuedJob, future: Future[JobResult]) -> None:
        try:
            result = future.result()
        except Exception as e:
            result = JobResult(
                job_id=job.job_id,
                matrix_index=job.matrix_entry.index,
                matrix_name=job.matrix_entry.name,
                status=JobStatus.ERROR,
                error_message=str(e),
            )
        if self._run:
            self._run.results[job.queue_key] = result
        success = result.status == JobStatus.PASSED
        self.queue.mark_completed(job, success=success)
        if result.status == JobStatus.TIMEOUT:
            event_type = JobEventType.JOB_TIMEOUT
        elif success:
            event_type = JobEventType.JOB_COMPLETED
        else:
            event_type = JobEventType.JOB_FAILED
        self._emit(event_type, job, result=result)
        logger.info(
            "%s: %s (%.1fs)",
            "PASSED" if success else "FAILED",
            job.matrix_entry.name,
            getattr(result, "duration_seconds", 0.0),
        )
        # Do not clean up act containers here when running in parallel:
        # cleanup_act_containers() removes ALL act-* containers, which would
        # kill containers still in use by other running jobs (causing
        # "No such container" when act tries to docker cp). Cleanup runs
        # once at the end of the run in execute() finally block.

    def _check_resources(self) -> None:
        ok, warnings = self._resource_monitor.check_thresholds(
            cpu_threshold=self.config.cpu_threshold,
            memory_threshold=self.config.memory_threshold,
            disk_min_gb=self.config.disk_min_free_gb,
        )
        if not ok and self._state == OrchestratorState.RUNNING:
            logger.warning("Resource pressure, pausing dispatch: %s", warnings)
            self._state = OrchestratorState.PAUSED
            jobs = self.queue.get_all_jobs()
            if jobs:
                self._emit(
                    JobEventType.RESOURCE_WARNING,
                    jobs[0],
                    warnings=warnings,
                )
        elif ok and self._state == OrchestratorState.PAUSED:
            logger.info("Resources recovered, resuming dispatch")
            self._state = OrchestratorState.RUNNING

    def _cleanup_all_containers(self) -> None:
        try:
            count = self._docker.cleanup_act_containers()
            if count > 0:
                logger.info("Cleaned up %d act containers", count)
        except Exception as e:
            logger.warning("Final cleanup error: %s", e)

    def _handle_sigint(self, signum: int, frame: object) -> None:
        logger.info("Ctrl+C received, shutting down gracefully...")
        self._state = OrchestratorState.CANCELLING
        self._shutdown_event.set()
        self.queue.cancel_all()

    def cancel(self) -> None:
        logger.info("Cancellation requested")
        self._state = OrchestratorState.CANCELLING
        self._shutdown_event.set()
        self.queue.cancel_all()

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def execution_id(self) -> str | None:
        return self._run.execution_id if self._run else None

    @property
    def active_workers(self) -> int:
        return len([f for f in self._futures.values() if not f.done()])

    def get_status(self) -> dict[str, Any]:
        if not self._run:
            return {"state": "idle"}
        snap = self._resource_monitor.snapshot()
        return {
            "execution_id": self._run.execution_id,
            "state": self._state.value,
            "total_jobs": self.queue.total_jobs,
            "completed": self.queue.completed_count,
            "running": self.queue.running_count,
            "pending": self.queue.pending_count,
            "passed": self.queue.passed_count,
            "failed": self.queue.failed_count,
            "current_priority": self.queue.current_priority,
            "active_workers": self.active_workers,
            "duration_seconds": self._run.duration_seconds,
            "resources": snap.summary(),
            "priority_summary": self.queue.get_priority_summary(),
        }
