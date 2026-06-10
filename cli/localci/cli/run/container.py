"""Dependency injection container for ``localci run``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from localci.core.boost_cache import ensure_boost_cache
from localci.core.ccache_stats import get_ccache_stats
from localci.core.config import LocalCIConfig, resolve_cache_paths
from localci.core.executor import JobExecutor
from localci.core.orchestrator import OrchestratorConfig, ParallelExecutionManager
from localci.core.progress import ProgressTracker
from localci.core.queue import PriorityConfig
from localci.core.queue_builder import QueueBuilder
from localci.core.workflow import WorkflowAnalyzer


@dataclass
class RunDependencies:
    """Injectable collaborators for ``execute_run`` (pass via ``deps=`` in tests)."""

    workflow_analyzer: WorkflowAnalyzer
    queue_builder_factory: Callable[..., QueueBuilder]
    job_executor_factory: Callable[[Path], JobExecutor]
    parallel_manager_factory: Callable[..., ParallelExecutionManager]
    progress_tracker_factory: Callable[..., ProgressTracker]
    orchestrator_config_factory: Callable[[LocalCIConfig], OrchestratorConfig]
    priority_config_factory: Callable[[LocalCIConfig], PriorityConfig]
    ensure_boost_cache_fn: Callable[..., bool]
    get_ccache_stats_fn: Callable[..., str | None]
    resolve_cache_paths_fn: Callable[..., object]
    workflow_patcher: Callable[..., Path]


def build_run_container() -> RunDependencies:
    """Construct the default production dependency graph for ``localci run``."""
    from localci.cli.run.patcher import _write_patched_workflow

    return RunDependencies(
        workflow_analyzer=WorkflowAnalyzer(),
        queue_builder_factory=QueueBuilder,
        job_executor_factory=JobExecutor,
        parallel_manager_factory=ParallelExecutionManager,
        progress_tracker_factory=ProgressTracker,
        orchestrator_config_factory=OrchestratorConfig.from_config,
        priority_config_factory=PriorityConfig.from_config,
        ensure_boost_cache_fn=ensure_boost_cache,
        get_ccache_stats_fn=get_ccache_stats,
        resolve_cache_paths_fn=resolve_cache_paths,
        workflow_patcher=_write_patched_workflow,
    )
