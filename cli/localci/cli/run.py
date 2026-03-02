"""``localci run`` command.

Execute selected jobs locally via the parallel execution manager
(queue + orchestrator) with Docker containers.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import click
import re

from localci.core.executor import (
    ActNotFoundError,
    DockerNotAvailableError,
    JobExecutor,
)
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
from localci.utils.output import (
    console,
    print_error,
    print_info,
    print_key_value,
    print_success,
    print_warning,
)


@click.command()
@click.option(
    "--workflow",
    "-w",
    type=click.Path(exists=True),
    default=None,
    help="Workflow file (defaults to config value).",
)
@click.option(
    "--job",
    "-j",
    "jobs",
    multiple=True,
    help="Job index or name (can specify multiple).",
)
@click.option(
    "--platform",
    "-p",
    type=click.Choice(["linux", "windows", "macos"]),
    default=None,
    help="Run all jobs for a platform.",
)
@click.option("--compiler", type=str, default=None, help="Filter by compiler.")
@click.option(
    "--matrix",
    "-m",
    "matrix_filters",
    multiple=True,
    help="Matrix filter as key=value (repeatable).",
)
@click.option(
    "--parallel",
    type=int,
    default=None,
    help="Max parallel jobs (overrides config).",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Job timeout in seconds (overrides config).",
)
@click.option(
    "--dry-run", is_flag=True, help="Preview execution plan without running."
)
@click.option("--no-cache", is_flag=True, help="Disable build caching.")
@click.option(
    "--rebuild-image", is_flag=True, help="Force rebuild Docker image."
)
@click.option(
    "--keep-containers/--no-keep-containers",
    "keep_containers",
    default=None,
    help="Keep containers after execution (default: from config).",
)
@click.option(
    "--interactive", "-i", is_flag=True, help="Interactive job selection."
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Show verbose act output."
)
@click.option(
    "--github-token",
    "-t",
    "github_token",
    type=str,
    default=None,
    help="GitHub token for API access (or set GITHUB_TOKEN env var).",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Run in offline mode (no action downloads, requires pre-cached actions).",
)
@click.pass_context
def run(
    ctx: click.Context,
    workflow: str | None,
    jobs: tuple[str, ...],
    platform: str | None,
    compiler: str | None,
    matrix_filters: tuple[str, ...],
    parallel: int | None,
    timeout: int | None,
    dry_run: bool,
    no_cache: bool,
    rebuild_image: bool,
    keep_containers: bool | None,
    interactive: bool,
    verbose: bool,
    github_token: str | None,
    offline: bool,
) -> None:
    """Execute selected jobs locally with parallel execution."""
    cfg = ctx.obj["config"]

    effective_timeout = timeout or cfg.execution.timeout
    effective_parallel = parallel or cfg.parallel.max_jobs
    effective_keep_containers = (
        keep_containers if keep_containers is not None else cfg.execution.keep_containers
    )
    workflow_path = Path(workflow) if workflow else cfg.workflow
    project_dir = Path(".").resolve()

    gh_token = github_token or os.environ.get("GITHUB_TOKEN") or "local-ci-token"

    # ── 1. Parse the workflow ──────────────────────────────────────
    try:
        analyzer = WorkflowAnalyzer()
        wf = analyzer.analyze(workflow_path)
    except Exception as exc:
        print_error(f"Failed to parse workflow: {exc}")
        ctx.exit(1)

    # Collect (job_id, entry) pairs
    all_pairs: list[tuple[str, MatrixEntry]] = []
    for job_id, job in wf.jobs.items():
        for entry in job.matrix:
            all_pairs.append((job_id, entry))

    if not all_pairs:
        print_warning("No matrix entries found in workflow.")
        return

    # ── 2. Warn about not-yet-implemented flags ─────────────────────
    if no_cache:
        print_warning("--no-cache is not yet implemented; ignoring.")
    if rebuild_image:
        print_warning("--rebuild-image is not yet implemented; ignoring.")
    if interactive:
        print_warning("--interactive is not yet implemented; ignoring.")
    if matrix_filters:
        print_warning("--matrix filters are not yet implemented; ignoring.")

    # ── 3. Filter entries ──────────────────────────────────────────
    plat_map = {
        "linux": Platform.LINUX,
        "windows": Platform.WINDOWS,
        "macos": Platform.MACOS,
    }
    compiler_filter = compiler.lower() if compiler else None
    selected: list[tuple[str, MatrixEntry]] = list(all_pairs)
    if platform:
        target_plat = plat_map.get(platform)
        selected = [(jid, e) for jid, e in selected if e.platform == target_plat]

    if compiler_filter:
        selected = [
            (jid, e)
            for jid, e in selected
            if e.compiler.family.value == compiler_filter
        ]

    if jobs:
        seen: set[tuple[str, int]] = set()
        filtered_list: list[tuple[str, MatrixEntry]] = []
        for j in jobs:
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
        print_warning("No jobs match the given filters.")
        return

    # Build queue via QueueBuilder
    selected_set = {(jid, e.index) for jid, e in selected}
    job_filter_list = list({jid for jid, _ in selected})
    plat_filter = plat_map.get(platform) if platform else None
    matrix_include = (
        [f.model_dump(exclude_none=True) for f in cfg.matrix.include]
        if cfg.matrix.include
        else None
    )
    matrix_exclude = (
        [f.model_dump(exclude_none=True) for f in cfg.matrix.exclude]
        if cfg.matrix.exclude
        else None
    )
    priority_config = PriorityConfig.from_config(cfg)
    builder = QueueBuilder(wf, priority_config=priority_config)
    queue = builder.build(
        platform_filter=plat_filter,
        job_filter=job_filter_list,
        compiler_filter=compiler_filter,
        matrix_include=matrix_include,
        matrix_exclude=matrix_exclude,
        entries_include=selected_set,
    )

    # ── 4. Dry-run mode ───────────────────────────────────────────
    if dry_run:
        _print_execution_plan(queue, workflow_path, effective_timeout)
        return

    # ── 5. Preflight checks ───────────────────────────────────────
    logs_dir = Path(cfg.logging.directory)
    executor = JobExecutor(logs_dir=logs_dir)
    try:
        act_version = executor.check_act()
        print_info(f"Using {act_version}")
    except ActNotFoundError as exc:
        print_error(str(exc))
        ctx.exit(1)

    try:
        executor.check_docker()
    except DockerNotAvailableError as exc:
        print_error(str(exc))
        ctx.exit(1)

    # ── 6. Execute via orchestrator ────────────────────────────────
    orch_config = OrchestratorConfig(
        max_parallel=effective_parallel,
        job_timeout=effective_timeout,
        stop_on_first_failure=cfg.execution.stop_on_first_failure,
        keep_containers=effective_keep_containers,
        default_secrets={"GITHUB_TOKEN": gh_token},
        default_env={},
    )
    orchestrator = ParallelExecutionManager(
        queue=queue,
        workflow_file=workflow_path,
        project_dir=project_dir,
        config=orch_config,
        logs_dir=logs_dir,
        workflow_patcher=_write_patched_workflow,
    )

    status_file = logs_dir / "last-status.json"
    tracker = ProgressTracker(
        queue=queue,
        workflow_file=str(workflow_path),
        platform=platform or "linux",
        max_parallel=effective_parallel,
        status_file=status_file,
    )
    for job in queue.get_all_jobs():
        tracker.on_event(
            JobEvent(event_type=JobEventType.JOB_QUEUED, job=job)
        )

    orchestrator.add_listener(tracker.on_event)

    tracker.start_live()
    try:
        run = orchestrator.execute()
    finally:
        tracker.stop_live()

    tracker.set_execution_id(run.execution_id)
    tracker.write_status_file()

    # ── 7. Summary ────────────────────────────────────────────────
    summary = ExecutionSummary(
        execution_id=run.execution_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        results=list(run.results.values()),
    )
    tracker.print_summary(run)

    # Save results: both last-run.json and {execution_id}.json so
    # status --execution-id X and logs -e X can find this run
    logs_dir = cfg.logging.directory
    last_run_file = logs_dir / "last-run.json"
    execution_file = logs_dir / f"{summary.execution_id}.json"
    try:
        summary.save(last_run_file)
        summary.save(execution_file)
        print_info(f"Results saved to {last_run_file}")
        print_info(f"Execution ID: {summary.execution_id} (use with status -e or logs -e)")
    except Exception as exc:
        print_warning(f"Could not save results: {exc}")

    if not summary.all_passed:
        ctx.exit(1)


# ─── Helpers ───────────────────────────────────────────────────────


def _print_execution_plan(queue, workflow_path: Path, timeout: int) -> None:
    """Print dry-run execution plan from the queue."""
    from rich.table import Table

    print_info("Dry run - execution plan:")
    print_key_value("Workflow", str(workflow_path))
    print_key_value("Jobs", str(queue.total_jobs))
    print_key_value("Timeout", f"{timeout}s")
    console.print()

    table = Table(title=f"Execution plan: {queue.total_jobs} jobs")
    table.add_column("Priority", justify="center", style="dim")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Compiler", style="cyan")
    table.add_column("Image")
    for job in sorted(
        queue.get_all_jobs(),
        key=lambda j: (j.priority, j.matrix_entry.index),
    ):
        table.add_row(
            str(job.priority),
            str(job.matrix_entry.index),
            job.matrix_entry.name,
            f"{job.matrix_entry.compiler.family.value}-{job.matrix_entry.compiler.version}",
            job.image_tag or "none",
        )
    console.print(table)
    summary = queue.get_priority_summary()
    console.print("[bold]Priority levels:[/bold]")
    for pri, counts in sorted(summary.items()):
        console.print(f"  Priority {pri}: {counts['total']} jobs")
    console.print()


def _write_patched_workflow(
    workflow_path: Path, entry: MatrixEntry, image_tag: str | None = None
) -> Path:
    """Write a copy of the workflow with optional container and Codecov patches.

    When the workflow has container: ${{ matrix.container }}, act uses that image
    and ignores our -P mapping. If image_tag is set, replace this entry's container
    with image_tag. Always patch the Codecov step to skip upload when ACT is set
    (codecov.io often returns 403 when run under act). Patching is text-only to
    avoid YAML round-trip issues.
    """
    with open(workflow_path, encoding="utf-8") as f:
        lines = f.readlines()

    if image_tag:
        name_escaped = re.escape(entry.name)
        name_pattern = re.compile(r'name:\s*["\']?' + name_escaped + r'["\']?\s*$')
        name_idx = None
        for i, line in enumerate(lines):
            if name_pattern.search(line.strip()):
                name_idx = i
                break
        if name_idx is None:
            raise ValueError(f"Matrix entry name '{entry.name}' not found in workflow")

        # Use indentation of the matched name line so we work with any indent width
        name_line = lines[name_idx]
        name_indent = name_line[: len(name_line) - len(name_line.lstrip())]
        name_indent_len = len(name_indent)

        # Find block start: the "- " list item line that contains this name (go backward)
        block_start = name_idx
        while block_start > 0:
            block_start -= 1
            line = lines[block_start]
            line_indent = line[: len(line) - len(line.lstrip())]
            if line.strip().startswith("-") and len(line_indent) <= name_indent_len:
                break

        # Block end: next "- " at same indent as block_start, or first line with less indent
        list_item_indent = lines[block_start][: len(lines[block_start]) - len(lines[block_start].lstrip())]
        list_item_indent_len = len(list_item_indent)
        block_end = name_idx + 1
        while block_end < len(lines):
            line = lines[block_end]
            line_indent = line[: len(line) - len(line.lstrip())]
            if line_indent == list_item_indent and line.strip().startswith("-"):
                break
            if len(line_indent) < list_item_indent_len:
                break
            block_end += 1

        # Replace container within this block (container_pattern accepts any leading whitespace)
        container_pattern = re.compile(
            r"^(\s+)container:\s*[\"']?[^\"'\n]*[\"']?\s*$"
        )
        for i in range(block_start, block_end):
            mo = container_pattern.match(lines[i])
            if mo:
                lines[i] = f'{mo.group(1)}container: "{image_tag}"\n'
                break

    # Patch Codecov step: skip upload when running under act (codecov.io often returns 403)
    for i, line in enumerate(lines):
        if "https://codecov.io/bash" in line and "curl" in line:
            stripped = line.lstrip()
            if stripped.strip().startswith("bash <(curl") or "bash <(curl" in stripped:
                indent = line[: len(line) - len(line.lstrip())]
                rest = stripped.strip().rstrip()
                # Emit bash conditional so codecov upload runs only when not under act
                act_check = 'if [ -z "${ACT:-}" ] || [ "$ACT" != "true" ]; then '
                lines[i] = f"{indent}{act_check}{rest}; else echo \"Skipping Codecov upload (running under act).\"; fi\n"
            break

    fd, path = tempfile.mkstemp(suffix=".yml", prefix="localci-workflow-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return Path(path)
