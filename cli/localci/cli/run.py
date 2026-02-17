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
from localci.core.boost_cache import ensure_boost_cache
from localci.core.ccache_stats import get_ccache_stats
from localci.core.config import resolve_cache_paths
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
@click.option("--no-cache", is_flag=True, help="Disable build caching (ccache, boost, b2-build, cmake).")
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Override cache root directory (default: config cache.directory).",
)
@click.option(
    "--rebuild-image", is_flag=True, help="Force rebuild Docker image."
)
@click.option(
    "--keep-containers",
    is_flag=True,
    help="Keep containers after execution.",
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
    cache_dir: Path | None,
    rebuild_image: bool,
    keep_containers: bool,
    interactive: bool,
    verbose: bool,
    github_token: str | None,
    offline: bool,
) -> None:
    """Execute selected jobs locally with parallel execution."""
    cfg = ctx.obj["config"]

    effective_timeout = timeout or cfg.execution.timeout
    effective_parallel = parallel or cfg.parallel.max_jobs
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
        return

    # Collect (job_id, entry) pairs
    all_pairs: list[tuple[str, MatrixEntry]] = []
    for job_id, job in wf.jobs.items():
        for entry in job.matrix:
            all_pairs.append((job_id, entry))

    if not all_pairs:
        print_warning("No matrix entries found in workflow.")
        return

    # ── 2. Warn about not-yet-implemented flags ─────────────────────
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
    selected: list[tuple[str, MatrixEntry]] = list(all_pairs)
    if platform:
        target_plat = plat_map.get(platform)
        selected = [(jid, e) for jid, e in selected if e.platform == target_plat]

    if compiler:
        comp_lower = compiler.lower()
        selected = [
            (jid, e)
            for jid, e in selected
            if comp_lower in e.compiler.family.value.lower()
            or comp_lower in e.compiler.display_name.lower()
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
    compiler_filter = compiler.lower() if compiler else None
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
        return
    try:
        executor.check_docker()
    except DockerNotAvailableError as exc:
        print_error(str(exc))
        ctx.exit(1)
        return

    # ── 5b. Phase 2: ensure Boost cache (clone/fetch when enabled) ───
    if not no_cache and cfg.cache.enabled and cfg.cache.boost.enabled:
        ensure_boost_cache(cfg.cache, no_cache, cache_dir)

    # ── 6. Execute via orchestrator ────────────────────────────────
    orch_config = OrchestratorConfig(
        max_parallel=effective_parallel,
        job_timeout=effective_timeout,
        stop_on_first_failure=cfg.execution.stop_on_first_failure,
        keep_containers=keep_containers,
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
        cache_config=cfg.cache,
        no_cache=no_cache,
        cache_dir_override=cache_dir,
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

    # Issue 9: ccache stats after run (when cache enabled)
    if not no_cache and cfg.cache.enabled and cfg.cache.ccache.enabled:
        resolved = resolve_cache_paths(
            cfg.cache, False, cache_dir, None, None
        )
        if resolved and resolved.ccache_host is not None:
            stats = get_ccache_stats(resolved.ccache_host)
            if stats:
                print_info("ccache stats:")
                for line in stats.splitlines():
                    console.print(f"  {line}")

    results_file = logs_dir / "last-run.json"
    try:
        summary.save(results_file)
        print_info(f"Results saved to {results_file}")
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
    (codecov.io often returns 403 when run under act). When BOOST_ROOT is set (by
    localci cache), the Clone Boost step is skipped. Patching is text-only to
    avoid YAML round-trip issues.
    """
    with open(workflow_path, encoding="utf-8") as f:
        lines = f.readlines()

    # Patch Boost clone step: skip when BOOST_ROOT is set (localci provides cached Boost)
    for i, line in enumerate(lines):
        if "boost-clone" in line and ("uses:" in line or "cpp-actions/boost-clone" in line):
            # Find the start of this step (the "- name:" line)
            step_start = i
            while step_start > 0:
                prev = lines[step_start - 1]
                if re.match(r"^\s+-\s+name:\s*", prev):
                    step_start = step_start - 1
                    break
                step_start -= 1
            # Avoid adding if twice
            step_end = i + 1
            while step_end < len(lines) and re.match(r"^\s{6,}\S", lines[step_end]):
                step_end += 1
            has_boost_root_if = any(
                "BOOST_ROOT" in lines[j] for j in range(step_start, min(step_end, len(lines)))
            )
            if not has_boost_root_if:
                indent = line[: len(line) - len(line.lstrip())]
                if_line = f"{indent}if: ${{{{ env.BOOST_ROOT == '' }}}}\n"
                lines.insert(step_start + 1, if_line)
                step_end += 1  # one line inserted
                # When BOOST_ROOT is set, workflow needs boost-source for Patch step
                new_step = [
                    "      - name: Use cached Boost (BOOST_ROOT)\n",
                    "        if: ${{ env.BOOST_ROOT != '' }}\n",
                    '        run: rm -rf boost-source 2>/dev/null; ln -s "$BOOST_ROOT" boost-source\n',
                ]
                for j, new_line in enumerate(new_step):
                    lines.insert(step_end + 1 + j, new_line)
            break

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
        block_start = name_idx
        while block_start > 0 and not re.match(r"^\s{10}-\s", lines[block_start]):
            block_start -= 1
        block_end = name_idx + 1
        while block_end < len(lines) and not (
            re.match(r"^\s{10}-\s", lines[block_end])
            or re.match(r"^\s{4}\w", lines[block_end])
        ):
            block_end += 1
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


def _derive_image_tag(entry: MatrixEntry) -> str:
    """Derive a Docker image tag from a matrix entry.

    Always uses our built capy image names so act runs local images
    (e.g. capy-ubuntu-24.04-clang20-x86) instead of pulling ubuntu:24.04
    with linux/386, which does not exist. Uses container image or runs_on
    to get the OS label (e.g. ubuntu:24.04 -> ubuntu-24.04).
    """
    if entry.container.image:
        # e.g. "ubuntu:24.04" or "ubuntu:25.04" -> "ubuntu-24.04"
        img = entry.container.image.strip().lower()
        if ":" in img:
            os_label = img.replace(":", "-", 1)
        else:
            os_label = img
    else:
        os_label = entry.runs_on
    compiler_label = (
        f"{entry.compiler.family.value}{entry.compiler.version}"
    )
    base = f"capy-{os_label}-{compiler_label}"
    if entry.variant.coverage:
        base += "-cov"
    elif entry.variant.asan:
        base += "-asan"
    elif entry.variant.x86:
        base += "-x86"
    return f"{base}:latest"
