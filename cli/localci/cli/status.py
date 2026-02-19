"""``localci status`` command.

Show the progress of a running or completed execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from localci.core.results import ExecutionSummary
from localci.utils.output import (
    console,
    make_table,
    print_error,
    print_info,
    print_warning,
)


@click.command()
@click.option(
    "--execution-id",
    "-e",
    default=None,
    help="Specific execution ID.",
)
@click.option(
    "--follow",
    "-f",
    is_flag=True,
    help="Follow mode (live updates).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
@click.pass_context
def status(
    ctx: click.Context,
    execution_id: str | None,
    follow: bool,
    output_format: str,
) -> None:
    """Show execution progress."""
    if follow:
        print_warning("--follow is not yet implemented; showing current state only.")

    cfg = ctx.obj["config"]
    logs_dir: Path = cfg.logging.directory

    # Locate the results file
    if execution_id:
        results_file = logs_dir / f"{execution_id}.json"
    else:
        results_file = logs_dir / "last-run.json"

    if not results_file.exists():
        if execution_id:
            print_warning(f"No results found for execution: {execution_id}")
        else:
            print_warning(
                "No previous execution found. Run `localci run` first."
            )
        return

    try:
        summary = ExecutionSummary.load(results_file)
    except Exception as exc:
        print_error(f"Failed to load results: {exc}")
        ctx.exit(1)

    if output_format == "json":
        click.echo(json.dumps(summary.to_dict(), indent=2))
        return

    # Table output
    console.print()
    print_info(f"Execution: {summary.execution_id}")
    console.print()

    table = make_table(
        "#", "Name", "Status", "Duration", "Image",
        title="Job Results",
    )
    for r in sorted(summary.results, key=lambda x: x.matrix_index):
        status_style = {
            "passed": "[green]passed[/green]",
            "failed": "[red]failed[/red]",
            "timeout": "[yellow]timeout[/yellow]",
            "error": "[red]error[/red]",
            "cancelled": "[dim]cancelled[/dim]",
            "skipped": "[dim]skipped[/dim]",
        }.get(r.status.value, r.status.value)

        table.add_row(
            str(r.matrix_index),
            r.matrix_name,
            status_style,
            r.duration_display,
            r.image_used or "-",
        )

    console.print(table)
    console.print()

    # Summary line
    if summary.all_passed:
        console.print(
            f"[green bold]ALL PASSED[/green bold] "
            f"({summary.passed}/{summary.total} in "
            f"{summary.total_duration:.1f}s)"
        )
    else:
        console.print(
            f"[red bold]{summary.failed} FAILED, "
            f"{summary.errors} ERRORS[/red bold] "
            f"({summary.passed}/{summary.total} passed in "
            f"{summary.total_duration:.1f}s)"
        )
