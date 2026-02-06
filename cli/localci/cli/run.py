"""``localci run`` command.

Execute selected jobs locally via ``act`` with Docker containers.
"""

from __future__ import annotations

import click

from localci.utils.output import (
    console,
    print_info,
    print_key_value,
    print_not_implemented,
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
@click.option("--parallel", type=int, default=None, help="Max parallel jobs (overrides config).")
@click.option("--timeout", type=int, default=None, help="Job timeout in seconds (overrides config).")
@click.option("--dry-run", is_flag=True, help="Preview execution plan without running.")
@click.option("--no-cache", is_flag=True, help="Disable build caching.")
@click.option("--rebuild-image", is_flag=True, help="Force rebuild Docker image.")
@click.option("--keep-containers", is_flag=True, help="Keep containers after execution.")
@click.option("--interactive", "-i", is_flag=True, help="Interactive job selection.")
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
) -> None:
    """Execute selected jobs locally."""
    cfg = ctx.obj["config"]

    effective_parallel = parallel or cfg.parallel.max_jobs
    effective_timeout = timeout or cfg.execution.timeout

    if dry_run:
        print_info("Dry run – execution plan:")
        print_key_value("Workflow", str(workflow or cfg.workflow))
        print_key_value("Platform", platform or "from config")
        print_key_value("Jobs", ", ".join(jobs) if jobs else "all enabled")
        print_key_value("Compiler", compiler or "all")
        print_key_value("Matrix filters", ", ".join(matrix_filters) if matrix_filters else "none")
        print_key_value("Parallelism", str(effective_parallel))
        print_key_value("Timeout", f"{effective_timeout}s")
        print_key_value("Cache", "disabled" if no_cache else "enabled")
        print_key_value("Rebuild images", str(rebuild_image))
        print_key_value("Keep containers", str(keep_containers))
        return

    # TODO: Replace with JobExecutor + Orchestrator from Issues 5 & 7.
    print_not_implemented("run (job executor backend)")
