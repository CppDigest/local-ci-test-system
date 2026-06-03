"""Click entry point for ``localci run`` (options in ``click_options``, logic in ``run_flow``)."""

from __future__ import annotations

from pathlib import Path

import click

from localci.cli.run.click_options import run_options
from localci.cli.run.container import build_run_container
from localci.cli.run.params import RunOptions
from localci.cli.run.run_flow import execute_run


@click.command()
@run_options
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
    exit_code = execute_run(
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
    if exit_code:
        ctx.exit(exit_code)
