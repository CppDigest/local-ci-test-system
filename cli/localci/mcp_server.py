"""MCP server for Local CI (Phase 6).

Exposes workflow analysis, async run, status, logs, and cancel via the
Model Context Protocol so agents can trigger and monitor runs without the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from localci.core.config import find_config_file, load_config
from localci.core.models import JobEvent, JobEventType
from localci.core.orchestrator import (
    OrchestratorConfig,
    ParallelExecutionManager,
)
from localci.core.progress import ProgressTracker
from localci.core.queue import PriorityConfig
from localci.core.queue_builder import QueueBuilder
from localci.core.results import ExecutionSummary
from localci.core.workflow import MatrixEntry, Platform, WorkflowAnalyzer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Execution registry for async runs and cancel
# ---------------------------------------------------------------------------

_execution_registry: dict[str, dict[str, Any]] = {}
_registry_lock = threading.Lock()


def _project_dir_from_workflow(workflow_file: str) -> Path:
    """Resolve project root from workflow file path."""
    p = Path(workflow_file).resolve()
    if not p.is_file():
        p = Path.cwd() / workflow_file
        p = p.resolve()
    # If path is like .../project/.github/workflows/ci.yml, go up to project
    while p.parent != p:
        if p.name == "workflows" and p.parent.name == ".github":
            return p.parent.parent
        if (p / ".localci.yml").exists() or (p / ".localci.yaml").exists():
            return p
        p = p.parent
    return Path(workflow_file).resolve().parent


def _get_logs_dir(project_dir: Optional[Path] = None) -> Path:
    """Return logs directory from config (or default)."""
    start = project_dir or Path.cwd()
    config_path = find_config_file(start)
    if config_path:
        cfg = load_config(config_path)
        return Path(cfg.logging.directory)
    return Path.home() / ".localci" / "logs"


# ---------------------------------------------------------------------------
# analyze_workflow
# ---------------------------------------------------------------------------


def _analyze_workflow_impl(workflow_file: str, event: str = "push") -> dict[str, Any]:
    """Step 1 only: jobs, matrix_entries, dependencies."""
    path = Path(workflow_file)
    if not path.is_absolute():
        path = Path.cwd() / workflow_file
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    analyzer = WorkflowAnalyzer()
    wf = analyzer.analyze(path, event or "push")

    jobs = []
    for job in wf.jobs.values():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "matrix_count": job.total_configurations,
            "needs": job.needs,
        })

    matrix_entries = []
    for job in wf.jobs.values():
        for entry in job.matrix:
            matrix_entries.append({
                "job_id": job.id,
                "index": entry.index,
                "name": entry.name,
                "platform": entry.platform.value,
                "compiler": entry.compiler.display_name,
            })

    dependencies = wf.dependency_order()

    return {
        "jobs": jobs,
        "matrix_entries": matrix_entries,
        "dependencies": dependencies,
    }


# ---------------------------------------------------------------------------
# run_local_ci (async)
# ---------------------------------------------------------------------------


def _run_local_ci_impl(
    workflow_file: str,
    event: str = "push",
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Start execution in background; return execution_id and status_url."""
    config = config or {}
    project_dir = _project_dir_from_workflow(workflow_file)
    workflow_path = Path(workflow_file)
    if not workflow_path.is_absolute():
        workflow_path = (project_dir / workflow_file).resolve()
    if not workflow_path.exists():
        workflow_path = Path.cwd() / workflow_file
        workflow_path = workflow_path.resolve()
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_file}")

    try:
        cfg = load_config(find_config_file(project_dir))
    except Exception:
        cfg = load_config(None)

    # Override from MCP config
    effective_parallel = config.get("max_parallel") or cfg.parallel.max_jobs
    job_names: list[str] = config.get("jobs") or []
    matrix_filters: dict[str, Any] = config.get("matrix_filters") or {}
    job_priorities: dict[str, int] = config.get("job_priorities") or {}

    analyzer = WorkflowAnalyzer()
    wf = analyzer.analyze(workflow_path, event or "push")

    all_pairs: list[tuple[str, MatrixEntry]] = []
    for job_id, job in wf.jobs.items():
        for entry in job.matrix:
            all_pairs.append((job_id, entry))

    if not all_pairs:
        return {
            "execution_id": "",
            "status_url": "",
            "error": "No matrix entries found in workflow",
        }

    # Apply filters
    selected = list(all_pairs)
    if matrix_filters:
        for key, value in matrix_filters.items():
            key_lower = str(key).lower()
            value_str = str(value).lower()
            if key_lower == "platform":
                plat = Platform(value_str) if value_str in ("linux", "windows", "macos") else None
                if plat:
                    selected = [(jid, e) for jid, e in selected if e.platform == plat]
            elif key_lower == "compiler":
                selected = [
                    (jid, e)
                    for jid, e in selected
                    if value_str in e.compiler.family.value.lower()
                    or value_str in e.compiler.display_name.lower()
                ]
            elif key_lower == "version":
                selected = [(jid, e) for jid, e in selected if e.compiler.version == value_str]

    if job_names:
        seen: set[tuple[str, int]] = set()
        filtered_list: list[tuple[str, MatrixEntry]] = []
        for j in job_names:
            try:
                idx = int(j)
                for jid, e in selected:
                    if e.index == idx and (jid, e.index) not in seen:
                        filtered_list.append((jid, e))
                        seen.add((jid, e.index))
                continue
            except ValueError:
                pass
            j_lower = j.lower()
            for jid, e in selected:
                if j_lower in e.name.lower() and (jid, e.index) not in seen:
                    filtered_list.append((jid, e))
                    seen.add((jid, e.index))
        selected = filtered_list

    if not selected:
        return {
            "execution_id": "",
            "status_url": "",
            "error": "No jobs match the given filters",
        }

    selected_set = {(jid, e.index) for jid, e in selected}
    job_filter_list = list({jid for jid, _ in selected})
    priority_config = PriorityConfig(
        default_priority=5,
        explicit=job_priorities or dict(cfg.priorities) if hasattr(cfg, "priorities") else {},
        rules=[],
    )
    registry_path = project_dir / "image-registry.yml"
    if not registry_path.exists():
        registry_path = None
    builder = QueueBuilder(wf, priority_config=priority_config)
    queue = builder.build(
        platform_filter=None,
        job_filter=job_filter_list,
        compiler_filter=None,
        matrix_include=None,
        matrix_exclude=None,
        entries_include=selected_set,
        registry_path=registry_path,
    )

    logs_dir = Path(cfg.logging.directory)
    orch_config = OrchestratorConfig(
        max_parallel=effective_parallel,
        job_timeout=cfg.execution.timeout,
        stop_on_first_failure=cfg.execution.stop_on_first_failure,
        keep_containers=cfg.execution.keep_containers,
        default_secrets={"GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN") or "local-ci-token"},
        default_env={},
    )
    images_dir = project_dir / "images" / "capy"

    # Import here to avoid circular import and to use CLI patcher
    from localci.cli.run import _write_patched_workflow

    orchestrator = ParallelExecutionManager(
        queue=queue,
        workflow_file=workflow_path,
        project_dir=project_dir,
        config=orch_config,
        logs_dir=logs_dir,
        workflow_patcher=_write_patched_workflow,
        cache_config=cfg.cache,
        no_cache=False,
        cache_dir_override=None,
        registry_path=registry_path,
        images_dir=images_dir,
    )

    status_file = logs_dir / "last-status.json"
    tracker = ProgressTracker(
        queue=queue,
        workflow_file=str(workflow_path),
        platform="linux",
        max_parallel=effective_parallel,
        status_file=status_file,
    )
    for job in queue.get_all_jobs():
        tracker.on_event(JobEvent(event_type=JobEventType.JOB_QUEUED, job=job))
    orchestrator.add_listener(tracker.on_event)

    execution_id_holder: list[str] = []
    execution_id_for_cleanup: list[str] = []

    def on_run_started(run):
        execution_id_holder.append(run.execution_id)
        execution_id_for_cleanup.append(run.execution_id)

    def run_in_thread():
        if cfg.cache.enabled and cfg.cache.boost.enabled:
            from localci.core.boost_cache import ensure_boost_cache
            ensure_boost_cache(cfg.cache, no_cache=False, cache_dir_override=None)
        tracker.start_live()
        try:
            run = orchestrator.execute(on_run_started=on_run_started)
            tracker.set_execution_id(run.execution_id)
            tracker.write_status_file()
            summary = ExecutionSummary(
                execution_id=run.execution_id,
                started_at=run.started_at,
                finished_at=run.finished_at,
                results=list(run.results.values()),
            )
            summary.save(logs_dir / "last-run.json")
            summary.save(logs_dir / f"{run.execution_id}.json")
        finally:
            tracker.stop_live()
            with _registry_lock:
                for eid in execution_id_for_cleanup:
                    _execution_registry.pop(eid, None)

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

    # Wait briefly for execution_id to be set
    for _ in range(50):
        if execution_id_holder:
            break
        thread.join(timeout=0.1)
    execution_id = execution_id_holder[0] if execution_id_holder else ""

    if execution_id:
        with _registry_lock:
            _execution_registry[execution_id] = {
                "orchestrator": orchestrator,
                "tracker": tracker,
                "thread": thread,
                "logs_dir": logs_dir,
            }

    return {
        "execution_id": execution_id,
        "status_url": f"local://status?execution_id={execution_id}" if execution_id else "",
    }


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


def _get_status_impl(execution_id: str) -> dict[str, Any]:
    """Return progress, completed_jobs, failed_jobs, running_jobs, pending_jobs."""
    with _registry_lock:
        reg = _execution_registry.get(execution_id)
    if reg:
        tracker = reg.get("tracker")
        if tracker is not None:
            data = tracker.get_status_dict()
            return {
                "progress": data.get("progress", ""),
                "execution_id": data.get("execution_id", execution_id),
                "completed_jobs": data.get("completed_jobs", []),
                "failed_jobs": data.get("failed_jobs", []),
                "running_jobs": data.get("running_jobs", []),
                "pending_jobs": data.get("pending_jobs", []),
            }
        orch = reg["orchestrator"]
        status = orch.get_status()
        return {
            "progress": f"{status.get('completed', 0)}/{status.get('total_jobs', 0)} jobs completed",
            "execution_id": execution_id,
            "completed_jobs": [],
            "failed_jobs": [],
            "running_jobs": [],
            "pending_jobs": [],
            "state": status.get("state", "unknown"),
        }
    logs_dir = _get_logs_dir()
    status_file = logs_dir / "last-status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text())
            if data.get("execution_id") == execution_id:
                return {
                    "progress": data.get("progress", ""),
                    "execution_id": data.get("execution_id", execution_id),
                    "completed_jobs": data.get("completed_jobs", []),
                    "failed_jobs": data.get("failed_jobs", []),
                    "running_jobs": data.get("running_jobs", []),
                    "pending_jobs": data.get("pending_jobs", []),
                }
        except Exception:
            pass
    results_file = logs_dir / f"{execution_id}.json"
    if not results_file.exists():
        return {
            "progress": "0/0 jobs completed",
            "execution_id": execution_id,
            "completed_jobs": [],
            "failed_jobs": [],
            "running_jobs": [],
            "pending_jobs": [],
            "error": f"No status or results found for execution {execution_id}",
        }
    summary = ExecutionSummary.load(results_file)
    completed = [
        {
            "name": r.matrix_name,
            "index": r.matrix_index,
            "status": r.status.value,
            "duration_seconds": r.duration_seconds,
        }
        for r in summary.results
        if r.status.value == "passed"
    ]
    failed = [
        {
            "name": r.matrix_name,
            "index": r.matrix_index,
            "status": r.status.value,
            "error_message": r.error_message,
            "log_file": str(r.log_file) if r.log_file else None,
        }
        for r in summary.results
        if r.status.value in ("failed", "error", "timeout")
    ]
    return {
        "progress": f"{summary.completed}/{summary.total} jobs completed",
        "execution_id": execution_id,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "running_jobs": [],
        "pending_jobs": [],
    }


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------


def _get_logs_impl(
    execution_id: str,
    job_name: str,
    matrix_entry: Optional[str] = None,
) -> str:
    """Return log content for the given job."""
    logs_dir = _get_logs_dir()
    results_file = logs_dir / f"{execution_id}.json"
    if not results_file.exists():
        raise FileNotFoundError(f"No results found for execution: {execution_id}")
    summary = ExecutionSummary.load(results_file)
    match = None
    for r in summary.results:
        if matrix_entry:
            if str(r.matrix_index) == matrix_entry or r.matrix_name == matrix_entry:
                if job_name.lower() in r.matrix_name.lower() or job_name == r.job_id:
                    match = r
                    break
        else:
            if (
                job_name.lower() in r.matrix_name.lower()
                or str(r.matrix_index) == job_name
                or job_name == r.job_id
            ):
                match = r
                break
    if not match:
        available = ", ".join(f"{r.matrix_index}:{r.matrix_name}" for r in summary.results)
        raise ValueError(f"No job matching '{job_name}' (matrix_entry={matrix_entry}). Available: {available}")
    if not match.log_file or not match.log_file.exists():
        return f"(No log file for {match.matrix_name}; status={match.status.value})"
    return match.log_file.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# cancel_execution
# ---------------------------------------------------------------------------


def _cancel_execution_impl(execution_id: str) -> dict[str, Any]:
    """Cancel a running execution."""
    with _registry_lock:
        reg = _execution_registry.get(execution_id)
    if not reg:
        return {
            "cancelled": False,
            "message": f"No running execution found for {execution_id}. It may have already finished.",
        }
    try:
        reg["orchestrator"].cancel()
        return {"cancelled": True, "execution_id": execution_id}
    except Exception as e:
        return {"cancelled": False, "error": str(e)}


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

def create_mcp_app():
    """Create and return the FastMCP app with all tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError("MCP server requires the 'mcp' package. Install with: pip install mcp")

    mcp = FastMCP(
        "Local CI",
        json_response=True,
    )

    @mcp.tool()
    def analyze_workflow(workflow_file: str, event: str = "push") -> dict[str, Any]:
        """Execute Step 1: analyze workflow file and return jobs, matrix_entries, and dependencies.
        Input: workflow_file (path to e.g. .github/workflows/ci.yml), event (e.g. push, pull_request).
        """
        return _analyze_workflow_impl(workflow_file, event)

    @mcp.tool()
    def run_local_ci(
        workflow_file: str,
        event: str = "push",
        config: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Start a local CI run asynchronously. Returns execution_id and status_url immediately.
        Input: workflow_file, event, optional config (jobs, matrix_filters, max_parallel, job_priorities).
        Poll get_status(execution_id) for progress.
        """
        return _run_local_ci_impl(workflow_file, event, config)

    @mcp.tool()
    def get_status(execution_id: str) -> dict[str, Any]:
        """Get execution status: progress, completed_jobs, failed_jobs, running_jobs, pending_jobs."""
        return _get_status_impl(execution_id)

    @mcp.tool()
    def get_logs(
        execution_id: str,
        job_name: str,
        matrix_entry: Optional[str] = None,
    ) -> str:
        """Get logs for a specific job. matrix_entry is optional (index or name)."""
        return _get_logs_impl(execution_id, job_name, matrix_entry)

    @mcp.tool()
    def cancel_execution(execution_id: str) -> dict[str, Any]:
        """Cancel a running execution."""
        return _cancel_execution_impl(execution_id)

    return mcp


def main() -> None:
    """Run the MCP server over stdio (for use by MCP clients that spawn this process)."""
    app = create_mcp_app()
    # Official MCP Python SDK: run() defaults to stdio when no transport given
    if hasattr(app, "run"):
        try:
            app.run(transport="stdio")
        except TypeError:
            app.run()


if __name__ == "__main__":
    main()
