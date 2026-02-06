"""``localci list`` command.

List available jobs and matrix entries with optional filtering by
platform, compiler, version, and enabled/disabled status.
"""

from __future__ import annotations

import click

from localci.utils.output import (
    console,
    print_info,
    print_not_implemented,
)


@click.command("list")
@click.option(
    "--workflow",
    "-w",
    type=click.Path(exists=True),
    default=None,
    help="Workflow file (defaults to config value).",
)
@click.option(
    "--platform",
    "-p",
    type=click.Choice(["linux", "windows", "macos", "all"]),
    default="all",
    help="Filter by platform.",
)
@click.option("--compiler", type=str, default=None, help="Filter by compiler (gcc, clang, msvc).")
@click.option("--version", "comp_version", type=str, default=None, help="Filter by compiler version.")
@click.option("--enabled", is_flag=True, help="Show only enabled jobs from config.")
@click.option("--disabled", is_flag=True, help="Show only disabled jobs.")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json", "simple"]),
    default="table",
    help="Output format.",
)
@click.pass_context
def list_cmd(
    ctx: click.Context,
    workflow: str | None,
    platform: str,
    compiler: str | None,
    comp_version: str | None,
    enabled: bool,
    disabled: bool,
    output_format: str,
) -> None:
    """List available jobs and matrix entries."""
    # TODO: Replace with WorkflowAnalyzer from Issue 2.
    print_not_implemented("list (workflow analyzer backend)")

    filters = []
    if platform != "all":
        filters.append(f"platform={platform}")
    if compiler:
        filters.append(f"compiler={compiler}")
    if comp_version:
        filters.append(f"version={comp_version}")
    if enabled:
        filters.append("enabled-only")
    if disabled:
        filters.append("disabled-only")

    filter_str = ", ".join(filters) if filters else "none"
    print_info(f"Would list jobs with filters: {filter_str}")
