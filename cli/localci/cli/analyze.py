"""``localci analyze`` command.

Parse a GitHub Actions workflow file and display its structure:
jobs, matrix configurations, dependencies, and platform breakdown.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import yaml

from localci.core.serialization import workflow_summary, workflow_to_json
from localci.core.workflow import Platform, WorkflowAnalyzer, WorkflowError
from localci.utils.output import (
    console,
    make_table,
    print_error,
    print_header,
    print_info,
    print_key_value,
)


# =====================================================================
# Table renderers
# =====================================================================


def _print_workflow_header(wf) -> None:
    """Print workflow overview."""
    console.print()
    print_key_value("Workflow", wf.name)
    print_key_value("File", str(wf.file_path))
    print_key_value("Events", ", ".join(wf.events))
    if wf.env:
        print_key_value("Environment vars", str(len(wf.env)))
    console.print()


def _print_jobs_table(wf) -> None:
    """Print summary of all jobs."""
    table = make_table("#", "Job ID", "Matrix", "Steps", "Depends On", "Timeout", title="Jobs")
    for i, job in enumerate(wf.jobs.values(), 1):
        table.add_row(
            str(i),
            job.id,
            str(job.total_configurations),
            str(len(job.steps)),
            ", ".join(job.needs) or "-",
            f"{job.timeout_minutes}m",
        )
    console.print(table)
    console.print()


def _print_matrix_table(wf) -> None:
    """Print all matrix configurations."""
    entries = wf.all_matrix_entries()
    if not entries:
        return

    table = make_table(
        "#", "Name", "Platform", "Compiler", "Container", "Build", "Variants",
        title=f"Matrix Configurations ({len(entries)})",
    )

    platform_color = {
        Platform.LINUX: "green",
        Platform.WINDOWS: "blue",
        Platform.MACOS: "yellow",
    }

    for entry in entries:
        color = platform_color.get(entry.platform, "white")
        table.add_row(
            str(entry.index),
            entry.name,
            f"[{color}]{entry.platform.value}[/{color}]",
            entry.compiler.display_name,
            entry.container.image or "-",
            entry.build_system.value,
            entry.variant.label,
        )

    console.print(table)
    console.print()


def _print_platform_summary(wf) -> None:
    """Print platform breakdown."""
    summary = wf.platform_summary()
    if not summary:
        return
    console.print("[bold]Platform Summary:[/bold]")
    for platform, count in summary.items():
        console.print(f"  {platform.value:10s}: {count} configuration(s)")
    console.print()


# =====================================================================
# Command
# =====================================================================


@click.command()
@click.argument("workflow", type=click.Path(exists=True))
@click.option(
    "--event",
    "-e",
    default="push",
    help="Git event type (push, pull_request, etc.).",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json", "yaml"]),
    default="table",
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    type=click.Path(),
    default=None,
    help="Save output to file instead of stdout.",
)
@click.option("--jobs-only", is_flag=True, help="Show only job names.")
@click.option("--matrix-only", is_flag=True, help="Show only matrix entries.")
@click.pass_context
def analyze(
    ctx: click.Context,
    workflow: str,
    event: str,
    output_format: str,
    output_file: str | None,
    jobs_only: bool,
    matrix_only: bool,
) -> None:
    """Parse workflow file and display structure.

    WORKFLOW is the path to a GitHub Actions YAML file
    (e.g. .github/workflows/ci.yml).
    """
    try:
        analyzer = WorkflowAnalyzer()
        wf = analyzer.analyze(Path(workflow), event)
    except WorkflowError as exc:
        print_error(str(exc))
        ctx.exit(1)
    except FileNotFoundError as exc:
        print_error(str(exc))
        ctx.exit(1)

    # ── JSON output ──────────────────────────────────────────────
    if output_format == "json":
        result = workflow_to_json(wf)
        if output_file:
            Path(output_file).write_text(result, encoding="utf-8")
            print_info(f"Saved to {output_file}")
        else:
            click.echo(result)
        return

    # ── YAML output ──────────────────────────────────────────────
    if output_format == "yaml":
        summary = workflow_summary(wf)
        result = yaml.dump(summary, default_flow_style=False, sort_keys=False)
        if output_file:
            Path(output_file).write_text(result, encoding="utf-8")
            print_info(f"Saved to {output_file}")
        else:
            click.echo(result)
        return

    # ── Table output (default) ───────────────────────────────────
    print_header(f"Workflow Analysis: {wf.name}")

    if jobs_only:
        for job_id in wf.jobs:
            console.print(f"  {job_id}")
        return

    if not matrix_only:
        _print_workflow_header(wf)
        _print_jobs_table(wf)

    _print_matrix_table(wf)
    _print_platform_summary(wf)
