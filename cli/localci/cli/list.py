"""``localci list`` command.

List available jobs and matrix entries with optional filtering by
platform, compiler, version, and enabled/disabled status.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from localci.core.workflow import (
    CompilerFamily,
    Platform,
    WorkflowAnalyzer,
    WorkflowError,
)
from localci.utils.output import (
    console,
    make_table,
    print_error,
    print_header,
    print_info,
)


# =====================================================================
# Helpers
# =====================================================================

_PLATFORM_MAP = {
    "linux": Platform.LINUX,
    "windows": Platform.WINDOWS,
    "macos": Platform.MACOS,
}

_COMPILER_MAP = {
    "gcc": CompilerFamily.GCC,
    "clang": CompilerFamily.CLANG,
    "apple-clang": CompilerFamily.APPLE_CLANG,
    "msvc": CompilerFamily.MSVC,
    "mingw": CompilerFamily.MINGW,
}

_PLATFORM_COLOR = {
    Platform.LINUX: "green",
    Platform.WINDOWS: "blue",
    Platform.MACOS: "yellow",
}


def _entry_matches_list(entry_name: str, names: list[str]) -> bool:
    """True if *entry_name* matches any string in *names* (case-insensitive).

    Match: exact equality or the list item is a substring of entry name,
    so config "GCC 15" matches entry "GCC 15: C++20".
    """
    entry_lower = entry_name.lower()
    for s in names:
        part = s.strip().lower()
        if not part:
            continue
        if entry_lower == part or part in entry_lower:
            return True
    return False


# =====================================================================
# Command
# =====================================================================


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
@click.option(
    "--compiler",
    type=str,
    default=None,
    help="Filter by compiler (gcc, clang, msvc, mingw, apple-clang).",
)
@click.option(
    "--version",
    "comp_version",
    type=str,
    default=None,
    help="Filter by compiler version.",
)
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
    # Resolve workflow path from argument or config
    cfg = ctx.obj or {}
    config = cfg.get("config")

    if workflow:
        wf_path = Path(workflow)
    elif config:
        wf_path = Path(config.workflow)
    else:
        print_error(
            "No workflow file specified. "
            "Use --workflow or create a .localci.yml config."
        )
        ctx.exit(1)
        return

    if not wf_path.exists():
        print_error(f"Workflow file not found: {wf_path}")
        ctx.exit(1)
        return

    # Parse
    try:
        analyzer = WorkflowAnalyzer()
        wf = analyzer.analyze(wf_path)
    except (WorkflowError, FileNotFoundError) as exc:
        print_error(str(exc))
        ctx.exit(1)
        return

    # Collect all matrix entries; include/exclude are applied later by entry.name
    entries = wf.all_matrix_entries()

    if platform != "all":
        target = _PLATFORM_MAP.get(platform)
        if target:
            entries = [e for e in entries if e.platform == target]

    if compiler:
        target_family = _COMPILER_MAP.get(compiler.lower())
        if target_family:
            entries = [e for e in entries if e.compiler.family == target_family]

    if comp_version:
        entries = [e for e in entries if e.compiler.version == comp_version]

    # Filter by config.jobs.include / config.jobs.exclude (--enabled / --disabled)
    if enabled or disabled:
        if config:
            include_names = config.jobs.include or []
            exclude_names = config.jobs.exclude or []

            if enabled:
                if include_names:
                    entries = [e for e in entries if _entry_matches_list(e.name, include_names)]
                elif exclude_names:
                    entries = [e for e in entries if not _entry_matches_list(e.name, exclude_names)]

            if disabled:
                if exclude_names:
                    entries = [e for e in entries if _entry_matches_list(e.name, exclude_names)]
                else:
                    entries = []
        else:
            # No config: --enabled/--disabled have no include/exclude list
            if disabled:
                entries = []

    # ── JSON output ──────────────────────────────────────────────
    if output_format == "json":
        data = [
            {
                "index": e.index,
                "name": e.name,
                "platform": e.platform.value,
                "compiler": e.compiler.display_name,
                "container": e.container.image,
                "build_system": e.build_system.value,
                "variant": e.variant.label,
                "runs_on": e.runs_on,
            }
            for e in entries
        ]
        click.echo(json.dumps(data, indent=2))
        return

    # ── Simple output ────────────────────────────────────────────
    if output_format == "simple":
        for e in entries:
            console.print(
                f"{e.index:>3}  {e.name:<35}  {e.platform.value:<8}  "
                f"{e.compiler.display_name:<18}  {e.variant.label}"
            )
        return

    # ── Table output (default) ───────────────────────────────────
    filter_desc = []
    if enabled:
        filter_desc.append("enabled")
    if disabled:
        filter_desc.append("disabled")
    if platform != "all":
        filter_desc.append(f"platform={platform}")
    if compiler:
        filter_desc.append(f"compiler={compiler}")
    if comp_version:
        filter_desc.append(f"version={comp_version}")

    title = f"Matrix Entries ({len(entries)})"
    if filter_desc:
        title += f"  [dim](filters: {', '.join(filter_desc)})[/dim]"

    print_header(title)

    if not entries:
        print_info("No entries match the given filters.")
        return

    table = make_table(
        "#", "Name", "Platform", "Compiler", "Container", "Build", "Variants",
    )

    for entry in entries:
        color = _PLATFORM_COLOR.get(entry.platform, "white")
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

    # Platform summary
    summary: dict[str, int] = {}
    for e in entries:
        key = e.platform.value
        summary[key] = summary.get(key, 0) + 1
    if summary:
        console.print("[bold]Matched:[/bold]", end="  ")
        parts = [f"{k}: {v}" for k, v in summary.items()]
        console.print("  ".join(parts))
        console.print()
