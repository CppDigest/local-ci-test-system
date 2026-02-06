"""``localci logs`` command.

View stdout/stderr logs for a specific job execution.
"""

from __future__ import annotations

import click

from localci.utils.output import print_info, print_not_implemented


@click.command()
@click.argument("job", required=True)
@click.option("--execution-id", "-e", default=None, help="Specific execution ID.")
@click.option("--follow", "-f", is_flag=True, help="Follow logs in real-time.")
@click.option("--tail", "-n", type=int, default=None, help="Show last N lines.")
@click.option(
    "--output",
    "-o",
    "output_file",
    type=click.Path(),
    default=None,
    help="Save logs to file.",
)
@click.option("--timestamps", "-t", is_flag=True, help="Show timestamps.")
@click.pass_context
def logs(
    ctx: click.Context,
    job: str,
    execution_id: str | None,
    follow: bool,
    tail: int | None,
    output_file: str | None,
    timestamps: bool,
) -> None:
    """View logs for a specific job.

    JOB is a job index (e.g. 5) or name (e.g. "GCC 15").
    """
    # TODO: Replace with log retrieval from Issue 5/7.
    print_not_implemented("logs (job executor backend)")
    print_info(f"Would show logs for job: {job}")
