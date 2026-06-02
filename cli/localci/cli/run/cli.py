"""Click entry point for ``localci run``."""

from __future__ import annotations

from pathlib import Path

import click

from localci.cli.run.container import build_run_container
from localci.cli.run.orchestrator import execute_run
from localci.cli.run.params import RunOptions


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
@click.option(
    "--no-cache", is_flag=True, help="Disable build caching (ccache, boost, b2-source, cmake)."
)
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
    cache_dir: Path | None,
    rebuild_image: bool,
    keep_containers: bool | None,
    interactive: bool,
    verbose: bool,
    github_token: str | None,
    offline: bool,
) -> None:
    """Execute selected jobs locally with parallel execution."""
    execute_run(
        ctx=ctx,
        cfg=ctx.obj["config"],
        options=RunOptions(
            workflow=workflow,
            jobs=jobs,
            platform=platform,
            compiler=compiler,
            matrix_filters=matrix_filters,
            parallel=parallel,
            timeout=timeout,
            dry_run=dry_run,
            no_cache=no_cache,
            cache_dir=cache_dir,
            rebuild_image=rebuild_image,
            keep_containers=keep_containers,
            interactive=interactive,
            verbose=verbose,
            github_token=github_token,
            offline=offline,
        ),
        deps=build_run_container(),
    )
