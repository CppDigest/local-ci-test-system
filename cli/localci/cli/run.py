"""``localci run`` command.

Execute selected jobs locally via ``act`` with Docker containers.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import click
import re

from localci.core.command_builder import ActCommandBuilder
from localci.core.executor import (
    ActNotFoundError,
    DockerNotAvailableError,
    JobExecutor,
    JobStatus,
)
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
    rebuild_image: bool,
    keep_containers: bool,
    interactive: bool,
    verbose: bool,
    github_token: str | None,
    offline: bool,
) -> None:
    """Execute selected jobs locally."""
    cfg = ctx.obj["config"]

    effective_timeout = timeout or cfg.execution.timeout
    workflow_path = Path(workflow) if workflow else cfg.workflow
    
    # Resolve GitHub token: CLI flag > env var > default
    gh_token = github_token or os.environ.get("GITHUB_TOKEN") or "local-ci-token"

    # ── 1. Parse the workflow ──────────────────────────────────────
    try:
        analyzer = WorkflowAnalyzer()
        wf = analyzer.analyze(workflow_path)
    except Exception as exc:
        print_error(f"Failed to parse workflow: {exc}")
        ctx.exit(1)
        return

    # Collect all matrix entries across jobs
    all_entries = []
    for job in wf.jobs.values():
        all_entries.extend(job.matrix)

    if not all_entries:
        print_warning("No matrix entries found in workflow.")
        return

    # ── 2. Warn about not-yet-implemented flags ─────────────────────
    if no_cache:
        print_warning("--no-cache is not yet implemented; ignoring.")
    if rebuild_image:
        print_warning("--rebuild-image is not yet implemented; ignoring.")
    if keep_containers:
        print_warning(
            "--keep-containers is not yet implemented; ignoring."
        )
    if interactive:
        print_warning("--interactive is not yet implemented; ignoring.")
    if matrix_filters:
        print_warning(
            "--matrix filters are not yet implemented; ignoring."
        )

    # ── 3. Filter entries ──────────────────────────────────────────
    selected = list(all_entries)

    # Platform filter
    if platform:
        plat_map = {
            "linux": Platform.LINUX,
            "windows": Platform.WINDOWS,
            "macos": Platform.MACOS,
        }
        target_plat = plat_map.get(platform)
        selected = [e for e in selected if e.platform == target_plat]

    # Compiler filter
    if compiler:
        comp_lower = compiler.lower()
        selected = [
            e
            for e in selected
            if comp_lower in e.compiler.family.value.lower()
            or comp_lower in e.compiler.display_name.lower()
        ]

    # Job name/index filter
    if jobs:
        seen_indices: set[int] = set()
        filtered: list = []
        for j in jobs:
            # Try as index
            try:
                idx = int(j)
                for e in selected:
                    if e.index == idx and e.index not in seen_indices:
                        filtered.append(e)
                        seen_indices.add(e.index)
                continue
            except ValueError:
                pass
            # Try as name substring
            j_lower = j.lower()
            for e in selected:
                if j_lower in e.name.lower() and e.index not in seen_indices:
                    filtered.append(e)
                    seen_indices.add(e.index)
        selected = filtered

    if not selected:
        print_warning("No jobs match the given filters.")
        return

    # ── 4. Dry-run mode ───────────────────────────────────────────
    if dry_run:
        print_info("Dry run - execution plan:")
        print_key_value("Workflow", str(workflow_path))
        print_key_value("Jobs", str(len(selected)))
        print_key_value("Timeout", f"{effective_timeout}s")
        console.print()

        builder = ActCommandBuilder(
            workflow_file=workflow_path,
            project_dir=Path("."),
            default_secrets={"GITHUB_TOKEN": gh_token},
            offline=offline,
        )
        for entry in selected:
            cmd = builder.build(entry, dryrun=True, verbose=verbose)
            console.print(f"  [bold]{entry.name}[/bold]")
            console.print(f"    {cmd.display()}")
            console.print()
            # Clean up the temp event file created by build()
            if cmd.event_file and cmd.event_file.exists():
                cmd.event_file.unlink(missing_ok=True)
        return

    # ── 5. Preflight checks ───────────────────────────────────────
    executor = JobExecutor(
        logs_dir=cfg.logging.directory,
    )

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

    # ── 6. Execute jobs ───────────────────────────────────────────
    summary = ExecutionSummary(
        execution_id=str(uuid.uuid4())[:8],
        started_at=datetime.now(),
    )

    builder = ActCommandBuilder(
        workflow_file=workflow_path,
        project_dir=Path("."),
        default_secrets={"GITHUB_TOKEN": gh_token},
        offline=offline,
    )

    console.print()
    print_info(f"Running {len(selected)} job(s)...")
    console.print()

    for entry in selected:
        # Build image tag based on entry data
        image_tag = _derive_image_tag(entry)
        # When the workflow sets container:, act uses it and ignores -P. Patch the
        # workflow so this matrix entry's container is our capy image. Also patch
        # for coverage jobs so the Codecov step skips upload under act (codecov.io 403).
        workflow_file_override: Path | None = None
        need_container_patch = (
            entry.container.image
            and image_tag
            and str(image_tag).startswith("capy-")
        )
        need_coverage_patch = entry.variant.coverage
        if need_container_patch or need_coverage_patch:
            try:
                workflow_file_override = _write_patched_workflow(
                    workflow_path,
                    entry,
                    image_tag=image_tag if need_container_patch else None,
                )
            except Exception as exc:
                print_warning(
                    f"Could not patch workflow for {entry.name}: {exc}; "
                    "act may use workflow container image."
                )

        cmd = builder.build(
            entry,
            image_tag=image_tag,
            verbose=verbose,
            workflow_file=workflow_file_override,
        )

        console.print(f"[bold]▶ {entry.name}[/bold]")
        console.print(f"  Command: [muted]{cmd.display()}[/muted]")
        console.print()

        result = executor.run(
            cmd=cmd,
            matrix_index=entry.index,
            matrix_name=entry.name,
            timeout=effective_timeout,
            stream_output=True,
        )

        if workflow_file_override is not None and workflow_file_override.exists():
            try:
                workflow_file_override.unlink()
            except OSError:
                pass

        summary.results.append(result)

        # Print result
        if result.success:
            print_success(
                f"{entry.name}: PASSED ({result.duration_display})"
            )
        elif result.status == JobStatus.TIMEOUT:
            print_warning(
                f"{entry.name}: TIMEOUT ({result.duration_display})"
            )
        elif result.status == JobStatus.ERROR:
            print_error(f"{entry.name}: ERROR - {result.error_message}")
        else:
            print_error(
                f"{entry.name}: FAILED ({result.duration_display})"
            )
            if result.error_message:
                console.print(f"  [muted]{result.error_message}[/muted]")
        console.print()

        # Stop-on-first-failure
        if (
            cfg.execution.stop_on_first_failure
            and result.status == JobStatus.FAILED
        ):
            print_warning("Stopping on first failure.")
            break

    # ── 7. Summary ────────────────────────────────────────────────
    summary.finished_at = datetime.now()

    console.print(summary.summary_report())

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

    # Exit code
    if not summary.all_passed:
        ctx.exit(1)
        return


# ─── Helpers ───────────────────────────────────────────────────────


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


def _derive_image_tag(entry: MatrixEntry) -> str | None:
    """Derive a Docker image tag from a matrix entry.

    Always uses our built capy image names so act runs local images
    (e.g. capy-ubuntu-24.04-clang20-x86) instead of pulling ubuntu:24.04
    with linux/386, which does not exist. Uses container image or runs_on
    to get the OS label (e.g. ubuntu:24.04 -> ubuntu-24.04).
    Returns None for non-Linux platforms (e.g. windows, macos) when
    container.image is empty, so callers do not add invalid capy image mappings.
    """
    if entry.container.image:
        # e.g. "ubuntu:24.04" or "ubuntu:25.04" -> "ubuntu-24.04"
        img = entry.container.image.strip().lower()
        if ":" in img:
            os_label = img.replace(":", "-", 1)
        else:
            os_label = img
    else:
        runs_on = entry.runs_on
        if runs_on.startswith("windows") or runs_on.startswith("macos"):
            return None
        if "ubuntu" not in runs_on.lower() and runs_on != "linux":
            return None
        os_label = runs_on
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
