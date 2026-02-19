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
@click.option("--no-cache", is_flag=True, help="Disable build caching (ccache, boost, b2-source, cmake).")
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
    registry_path = project_dir / "image-registry.yml"
    if not registry_path.exists():
        registry_path = None
    builder = QueueBuilder(wf, priority_config=priority_config)
    queue = builder.build(
        platform_filter=plat_filter,
        job_filter=job_filter_list,
        compiler_filter=compiler_filter,
        matrix_include=matrix_include,
        matrix_exclude=matrix_exclude,
        entries_include=selected_set,
        registry_path=registry_path,
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
    workflow_path: Path,
    entry: MatrixEntry,
    image_tag: str | None = None,
    job_id: str | None = None,
    container_mount_options: str | None = None,
) -> Path:
    """Write a copy of the workflow with optional container and Codecov patches.

    Patches applied (all text-only, no YAML round-trip):

    * **container image** – replaces ``container: ${{ matrix.container }}`` with
      *image_tag* so act uses the locally-built image instead of the raw base image.
    * **Patch Boost step** – when LOCALCI_B2_SOURCE_DIR is set, replaces
      ``cp -rL boost-source boost-root`` with cache-hit/miss logic:
      cache-hit: boost-root is a symlink to the stable cached tree so b2's
      bin.v2 artifacts survive across runs (incremental builds);
      cache-miss: standard cp -rL, then seed the cache from the dereferenced copy.
    * **b2 bootstrap skip** – injects a step before ``b2-workflow`` that stubs
      out ``bootstrap.sh`` when the b2 binary is already in the cache
      (mirrors https://github.com/iTinkerBell/cpp-actions/commit/671009a).
    * **container options** – injects bind-mount ``-v`` flags into the job's
      ``container.options`` so the job container gets the cache mounts (act does
      not forward ``--container-options`` to the job container when the workflow
      declares ``container:``).
    * **Codecov** – skips the codecov upload when running under act (codecov.io
      often returns 403 in local runs).
    """
    with open(workflow_path, encoding="utf-8") as f:
        lines = f.readlines()

    # Inject cache mount options into job container so act applies them to the job container
    if job_id and container_mount_options:
        job_header = re.compile(r"^\s{2}" + re.escape(job_id) + r"\s*:\s*$")
        for i, line in enumerate(lines):
            if not job_header.match(line):
                continue
            # We're in job_id; find "container:" then "options:" in this job (indent >= 4)
            for j in range(i + 1, len(lines)):
                row = lines[j]
                if row.strip() and (len(row) - len(row.lstrip())) <= 2:
                    break  # next job or top-level key
                if re.match(r"^\s+container\s*:\s*$", row):
                    for k in range(j + 1, min(j + 10, len(lines))):
                        opt_match = re.match(r"^(\s+)options\s*:\s*(.*)$", lines[k])
                        if opt_match:
                            existing = opt_match.group(2).strip().strip('"\'')
                            new_val = f"{existing} {container_mount_options}".strip()
                            lines[k] = f'{opt_match.group(1)}options: "{new_val}"\n'
                            break
                    break
            break

    # Patch Boost patch step: when LOCALCI_B2_SOURCE_DIR is set, reuse the cached boost-root
    # on subsequent runs so b2 sees stable header timestamps and rebuilds only what changed.
    #
    # Cache-hit path: headers are left untouched (timestamps unchanged) so b2 does an
    # incremental build.  Only the libs/capy slot is cleared so the current capy source
    # gets linked in by the workflow.  boost-root is a symlink to the cache directory.
    #
    # Cache-miss path (first run): standard cp -rL creates boost-root (resolves all
    # symlinks so the tree is self-contained), then we seed the cache from that clean
    # copy.  Seeding from boost-root (no symlinks) ensures future cp -a calls never
    # hit the "cannot overwrite directory with non-directory" conflict caused by
    # git-tracked symlinks inside boost-source.
    for i, line in enumerate(lines):
        if "cp -rL boost-source boost-root" in line and "LOCALCI_B2_SOURCE_DIR" not in line:
            ind = line[: len(line) - len(line.lstrip())]
            lines[i] = (
                f'{ind}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ] && [ -f "${{LOCALCI_B2_SOURCE_DIR}}/Jamroot" ]; then\n'
                f'{ind}  # Cache hit: leave headers untouched (stable timestamps) so b2 builds incrementally\n'
                f'{ind}  rm -rf "${{LOCALCI_B2_SOURCE_DIR}}/libs/capy" 2>/dev/null || true\n'
                f'{ind}  ln -sfn "${{LOCALCI_B2_SOURCE_DIR}}" boost-root\n'
                f'{ind}else\n'
                f'{ind}  cp -rL boost-source boost-root\n'
                f'{ind}  if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ]; then\n'
                f'{ind}    mkdir -p "${{LOCALCI_B2_SOURCE_DIR}}"\n'
                f'{ind}    cp -a boost-root/. "${{LOCALCI_B2_SOURCE_DIR}}/"\n'
                f'{ind}  fi\n'
                f'{ind}fi\n'
            )
            break

    # Patch: inject a "Restore capy timestamps" step before the Patch Boost step.
    #
    # git checkout sets every file's mtime to "now", so without this patch b2 would see
    # all capy source files as newer than their bin.v2 objects and recompile everything.
    # Instead, we save a content-hash snapshot (mtime + sha256) of capy C++ source files
    # in b2-source/.capy-file-stats after each build.  On the next run we restore the
    # saved mtime for any file whose sha256 hasn't changed.  Only files with a different
    # hash (actually modified) keep the fresh checkout mtime, so b2 rebuilds exactly those.
    for i, line in enumerate(lines):
        if re.match(r"^\s+-\s+name:\s+Patch Boost", line):
            already_patched = any(
                "capy-file-stats" in lines[j]
                for j in range(max(0, i - 15), i)
            )
            if not already_patched:
                new_step = [
                    "      - name: Restore capy source file timestamps\n",
                    "        run: |\n",
                    '          if [ -n "${LOCALCI_B2_SOURCE_DIR:-}" ] && [ -f "${LOCALCI_B2_SOURCE_DIR}/.capy-file-stats" ]; then\n',
                    "            while IFS=' ' read -r saved_mtime fhash relpath; do\n",
                    '              [ -f "capy-root/$relpath" ] || continue\n',
                    '              curr=$(sha256sum "capy-root/$relpath" 2>/dev/null | cut -d\' \' -f1)\n',
                    '              [ "$curr" = "$fhash" ] && touch -d "@$saved_mtime" "capy-root/$relpath" 2>/dev/null || true\n',
                    '            done < "${LOCALCI_B2_SOURCE_DIR}/.capy-file-stats"\n',
                    '          fi\n',
                ]
                for j, new_line in enumerate(new_step):
                    lines.insert(i + j, new_line)
            break

    # Patch: use cp -rp (preserves timestamps) instead of cp -r when copying capy source
    # into boost-root, so the restored mtimes survive into the b2 build.  Also save a
    # content-hash snapshot of all capy C++ source files to b2-source/.capy-file-stats
    # so the next run's restore step knows which files actually changed.
    for i, line in enumerate(lines):
        if 'cp -r "$workspace_root"' in line and "libs/" in line:
            ind = line[: len(line) - len(line.lstrip())]
            lines[i] = (
                f'{ind}cp -rp "$workspace_root"/capy-root "libs/$module"\n'
                f'{ind}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ]; then\n'
                f'{ind}  find "$workspace_root/capy-root" -type f \\( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.ipp" \\) |\n'
                f'{ind}  while IFS= read -r f; do\n'
                f'{ind}    mtime=$(stat -c "%Y" "$f")\n'
                f'{ind}    fhash=$(sha256sum "$f" | cut -d" " -f1)\n'
                f'{ind}    echo "$mtime $fhash ${{f#$workspace_root/capy-root/}}"\n'
                f'{ind}  done > "${{LOCALCI_B2_SOURCE_DIR}}/.capy-file-stats"\n'
                f'{ind}fi\n'
            )
            break

    # Patch: inject a step before b2-workflow to skip bootstrap when the b2 binary is
    # already in the b2-source cache.  b2's bootstrap.sh compiles the b2 engine from C++
    # (~20s per run).  On a cache-hit boost-root is a symlink to b2-source, so b2-source/b2
    # from the previous run is already at boost-root/b2.  We stub bootstrap.sh to a no-op
    # shell script so b2-workflow skips recompilation and uses the cached binary directly.
    # This mirrors the iTinkerBell/cpp-actions fork optimisation:
    # https://github.com/iTinkerBell/cpp-actions/commit/671009a
    for i, line in enumerate(lines):
        if "b2-workflow" in line and "uses:" in line:
            # Walk back from the uses: line to find the step's leading "- name:" line
            step_start = i
            while step_start > 0:
                if re.match(r"^\s+-\s+name:\s*", lines[step_start]):
                    break
                step_start -= 1
            # Idempotency: skip if already patched
            already_patched = any(
                "LOCALCI_B2_SOURCE_DIR" in lines[j] and "bootstrap" in lines[j]
                for j in range(max(0, step_start - 10), step_start)
            )
            if not already_patched:
                new_step = [
                    "      - name: Skip b2 bootstrap (b2 binary cached)\n",
                    "        run: |\n",
                    '          if [ -n "${LOCALCI_B2_SOURCE_DIR:-}" ] && [ -f "${LOCALCI_B2_SOURCE_DIR}/b2" ]; then\n',
                    "            printf '#!/bin/sh\\necho \"b2 binary cached, skipping bootstrap.\"\\n' > boost-root/bootstrap.sh\n",
                    "            chmod +x boost-root/bootstrap.sh\n",
                    "          fi\n",
                ]
                for j, new_line in enumerate(new_step):
                    lines.insert(step_start + j, new_line)
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
