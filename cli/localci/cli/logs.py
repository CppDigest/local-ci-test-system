"""``localci logs`` command.

View stdout/stderr logs for a specific job execution.
"""

from __future__ import annotations

from pathlib import Path

import click

from localci.core.results import ExecutionSummary
from localci.utils.output import (
    console,
    print_error,
    print_info,
    print_warning,
)


@click.command()
@click.argument("job", required=True)
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
    help="Follow logs in real-time (not yet implemented; shows snapshot only).",
)
@click.option(
    "--tail",
    "-n",
    type=int,
    default=None,
    help="Show last N lines.",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    type=click.Path(),
    default=None,
    help="Save logs to file.",
)
@click.option(
    "--timestamps",
    "-t",
    is_flag=True,
    help="Show timestamps.",
)
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
    if follow:
        print_warning("--follow is not yet implemented; showing log snapshot only.")
    if timestamps:
        print_warning("--timestamps is not yet implemented; ignoring.")

    cfg = ctx.obj["config"]
    logs_dir: Path = cfg.logging.directory

    # Locate the results file
    if execution_id:
        results_file = logs_dir / f"{execution_id}.json"
    else:
        results_file = logs_dir / "last-run.json"

    if not results_file.exists():
        if execution_id:
            print_warning(
                f"No results found for execution: {execution_id}. "
                f"Expected file: {results_file}"
            )
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

    # Find matching job result
    match = _find_job(summary, job)
    if not match:
        print_warning(f"No job matching '{job}' found in last execution.")
        print_info("Available jobs:")
        for r in summary.results:
            console.print(f"  [{r.matrix_index}] {r.matrix_name}")
        return

    # Read log file
    if not match.log_file or not match.log_file.exists():
        print_warning(
            f"No log file for '{match.matrix_name}'. "
            "The job may not have produced output."
        )
        return

    log_content = match.log_file.read_text(encoding="utf-8")
    lines = log_content.splitlines()

    # Apply tail
    if tail is not None and tail > 0:
        lines = lines[-tail:]

    content = "\n".join(lines)

    # Save to file
    if output_file:
        out = Path(output_file)
        out.write_text(content, encoding="utf-8")
        print_info(f"Logs saved to {out}")
        return

    # Print to console
    console.print(f"[bold]Logs for: {match.matrix_name}[/bold]")
    console.print(f"[muted]Status: {match.status.value}  |  "
                  f"Duration: {match.duration_display}  |  "
                  f"Exit code: {match.exit_code}[/muted]")
    console.print(f"[muted]Log file: {match.log_file}[/muted]")
    console.print()
    click.echo(content)


def _find_job(summary: ExecutionSummary, query: str):
    """Find a job by index or name.

    Matching priority:
    1. Exact index match (when *query* is an integer).
    2. Exact name match (case-insensitive).
    3. First substring match (case-insensitive).
    """
    # Try as index
    try:
        idx = int(query)
        for r in summary.results:
            if r.matrix_index == idx:
                return r
    except ValueError:
        pass

    query_lower = query.lower()

    # Exact name match first
    for r in summary.results:
        if r.matrix_name.lower() == query_lower:
            return r

    # Fallback to substring match
    for r in summary.results:
        if query_lower in r.matrix_name.lower():
            return r

    return None
