"""``localci status`` command.

Show the progress of a running or completed execution.
Supports MCP-style status from last-status.json and follow mode.
"""

from __future__ import annotations

import json
import time
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
    help="Follow mode: poll last-status.json until Ctrl+C (requires a running execution).",
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
    cfg = ctx.obj["config"]
    logs_dir = Path(cfg.logging.directory)

    # Prefer MCP status file when no execution-id specified
    status_file = logs_dir / "last-status.json"
    if not execution_id and status_file.exists():
        try:
            with open(status_file, encoding="utf-8") as f:
                status_data = json.load(f)
        except FileNotFoundError as exc:
            print_warning(f"Status file not found: {status_file}; {exc}")
            if follow:
                _follow_status(status_file, output_format)
                return
            status_data = None
        except json.JSONDecodeError as exc:
            print_warning(f"Invalid JSON in status file {status_file}: {exc}")
            if follow:
                _follow_status(status_file, output_format)
                return
            status_data = None
        except OSError as exc:
            print_warning(f"Could not read status file {status_file}: {exc}")
            if follow:
                _follow_status(status_file, output_format)
                return
            status_data = None
        if isinstance(status_data, dict) and "progress" in status_data:
            if output_format == "json":
                click.echo(json.dumps(status_data, indent=2))
                if follow:
                    _follow_status(status_file, output_format)
                return
            _print_status_table(status_data)
            if follow:
                _follow_status(status_file, output_format)
            return

    # Fall back to results file (last-run.json or {execution_id}.json)
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
        return

    if follow:
        print_warning("--follow only works with a live last-status.json (running execution); showing current state only.")
    if output_format == "json":
        click.echo(json.dumps(summary.to_dict(), indent=2))
        return

    # Table output from ExecutionSummary
    console.print()
    print_info(f"Execution: {summary.execution_id}")
    console.print()

    table = make_table(
        "#", "Name", "Status", "Duration", "Image",
        title="Job Results",
    )
    for r in sorted(summary.results, key=lambda x: (x.matrix_index is None, x.matrix_index)):
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


def _safe_float(value: object, default: float = 0.0) -> float:
    """Coerce *value* to float, returning *default* on None or parse failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _job_list(data: dict, key: str) -> list[dict]:
    """Return a list of dict items from *data[key]*, skipping non-dict entries."""
    raw = data.get(key) if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _print_status_table(data: object) -> None:
    """Render MCP status data as Rich table."""
    if not isinstance(data, dict):
        console.print("[bold red]Status data is not a valid dict.[/bold red]")
        return

    console.print()
    console.print(
        f"[bold]Execution:[/bold] {data.get('execution_id', 'unknown')}"
    )
    console.print(f"[bold]Progress:[/bold]  {data.get('progress', '')}")
    console.print(
        f"[bold]Elapsed:[/bold]   {_safe_float(data.get('elapsed_seconds')):.0f}s"
    )
    console.print()

    running = _job_list(data, "running_jobs")
    if running:
        console.print("[bold cyan]Running:[/bold cyan]")
        for job in running:
            name = job.get("name", "<unknown>")
            elapsed = _safe_float(job.get("elapsed_seconds"))
            step = job.get("current_step")
            if step:
                console.print(f"  ● {name} — {step} ({elapsed:.0f}s)")
            else:
                console.print(f"  ● {name} ({elapsed:.0f}s)")
        console.print()

    completed = _job_list(data, "completed_jobs")
    if completed:
        console.print(
            f"[bold green]Completed ({len(completed)}):[/bold green]"
        )
        for job in completed:
            name = job.get("name", "<unknown>")
            dur = _safe_float(job.get("duration_seconds"))
            console.print(f"  ✓ {name} ({dur:.0f}s)")
        console.print()

    failed = _job_list(data, "failed_jobs")
    if failed:
        console.print(f"[bold red]Failed ({len(failed)}):[/bold red]")
        for job in failed:
            name = job.get("name", "<unknown>")
            msg = job.get("error_message") or "unknown error"
            console.print(f"  ✗ {name}: {msg}")
        console.print()

    pending = _job_list(data, "pending_jobs")
    if pending:
        console.print(f"[dim]Pending ({len(pending)})[/dim]")
    console.print()


def _follow_status(status_file: Path, output_format: str) -> None:
    """Poll status file and refresh display until Ctrl+C."""
    console.print("\nFollowing... (Ctrl+C to stop)")
    try:
        last_data: dict | None = None
        while True:
            time.sleep(1)
            if not status_file.exists():
                continue
            try:
                with open(status_file, encoding="utf-8") as f:
                    new_data = json.load(f)
            except FileNotFoundError as exc:
                print_warning(f"Status file not found: {status_file}; {exc}")
                continue
            except json.JSONDecodeError as exc:
                print_warning(f"Invalid JSON in status file {status_file}: {exc}")
                continue
            except OSError as exc:
                print_warning(f"Could not read status file {status_file}: {exc}")
                continue
            if new_data != last_data:
                last_data = new_data
                console.clear()
                if output_format == "json":
                    click.echo(json.dumps(last_data, indent=2))
                else:
                    _print_status_table(last_data)
    except KeyboardInterrupt:
        pass
