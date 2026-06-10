"""Click option decorators for ``localci run`` (keeps ``cli.py`` entry ≤50 lines)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

F = TypeVar("F", bound=Callable[..., Any])


def run_options(command: F) -> F:
    """Apply all ``localci run`` CLI options to *command*."""
    opts: list[Callable[[F], F]] = [
        click.option(
            "--workflow",
            "-w",
            type=click.Path(exists=True),
            default=None,
            help="Workflow file (defaults to config value).",
        ),
        click.option(
            "--job",
            "-j",
            "jobs",
            multiple=True,
            help="Job index or name (can specify multiple).",
        ),
        click.option(
            "--platform",
            "-p",
            type=click.Choice(["linux", "windows", "macos"]),
            default=None,
            help="Run all jobs for a platform.",
        ),
        click.option("--compiler", type=str, default=None, help="Filter by compiler."),
        click.option(
            "--matrix",
            "-m",
            "matrix_filters",
            multiple=True,
            help="Matrix filter as key=value (repeatable).",
        ),
        click.option(
            "--parallel",
            type=int,
            default=None,
            help="Max parallel jobs (overrides config).",
        ),
        click.option(
            "--timeout",
            type=int,
            default=None,
            help="Job timeout in seconds (overrides config).",
        ),
        click.option(
            "--dry-run", is_flag=True, help="Preview execution plan without running."
        ),
        click.option(
            "--no-cache",
            is_flag=True,
            help="Disable build caching (ccache, boost, b2-source, cmake).",
        ),
        click.option(
            "--cache-dir",
            type=click.Path(path_type=Path, file_okay=False),
            default=None,
            help="Override cache root directory (default: config cache.directory).",
        ),
        click.option(
            "--rebuild-image", is_flag=True, help="Force rebuild Docker image."
        ),
        click.option(
            "--keep-containers/--no-keep-containers",
            "keep_containers",
            default=None,
            help="Keep containers after execution (default: from config).",
        ),
        click.option(
            "--interactive", "-i", is_flag=True, help="Interactive job selection."
        ),
        click.option("--verbose", "-v", is_flag=True, help="Show verbose act output."),
        click.option(
            "--github-token",
            "-t",
            "github_token",
            type=str,
            default=None,
            help="GitHub token for API access (or set GITHUB_TOKEN env var).",
        ),
        click.option(
            "--offline",
            is_flag=True,
            help=(
                "Run in offline mode (no action downloads, requires pre-cached actions)."
            ),
        ),
        click.pass_context,
    ]
    wrapped: F = command
    for opt in reversed(opts):
        wrapped = opt(wrapped)  # type: ignore[assignment]
    return wrapped
