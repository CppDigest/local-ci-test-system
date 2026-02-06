"""``localci status`` command.

Show the progress of a running or completed execution.
"""

from __future__ import annotations

import click

from localci.utils.output import print_info, print_not_implemented


@click.command()
@click.option("--execution-id", "-e", default=None, help="Specific execution ID.")
@click.option("--follow", "-f", is_flag=True, help="Follow mode (live updates).")
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
    # TODO: Replace with Orchestrator progress tracking from Issue 7.
    print_not_implemented("status (orchestrator backend)")
    if execution_id:
        print_info(f"Would show status for execution: {execution_id}")
    else:
        print_info("Would show status for the most recent execution")
