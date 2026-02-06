"""``localci analyze`` command.

Parse a GitHub Actions workflow file and display its structure:
jobs, matrix configurations, dependencies, and platform breakdown.
"""

from __future__ import annotations

import json

import click
import yaml

from localci.utils.output import (
    console,
    make_table,
    print_header,
    print_info,
    print_key_value,
    print_not_implemented,
)


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
    # TODO: Replace with WorkflowAnalyzer from Issue 2.
    print_not_implemented("analyze (workflow analyzer backend)")
    print_info(f"Would analyze: {workflow} (event={event}, format={output_format})")
