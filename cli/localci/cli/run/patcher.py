"""Workflow patcher and dry-run plan output for `localci run`."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from localci.core.config import LocalCIConfig
from localci.core.patch_pipeline import PatchContext, PatchPipeline
from localci.core.queue import PriorityJobQueue
from localci.core.workflow import MatrixEntry
from localci.utils.output import console, print_info, print_key_value


def _print_execution_plan(
    queue: PriorityJobQueue, workflow_path: Path, timeout: int
) -> None:
    """Print dry-run execution plan from the queue."""
    from rich.table import Table

    print_info("Dry run - execution plan:")
    print_key_value("Workflow", str(workflow_path))
    print_key_value("Jobs", str(queue.total_jobs))
    print_key_value("Timeout", f"{timeout}s")
    console.print()

    table = Table(title=f"Execution plan: {queue.total_jobs} jobs")
    table.add_column("Priority", justify="center", style="dim")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Compiler", style="cyan")
    table.add_column("Image")
    for job in sorted(
        queue.get_all_jobs(),
        key=lambda j: (j.priority, j.matrix_entry.index),
    ):
        table.add_row(
            str(job.priority),
            str(job.matrix_entry.index),
            job.matrix_entry.name,
            f"{job.matrix_entry.compiler.family.value}-{job.matrix_entry.compiler.version}",
            job.image_tag or "none",
        )
    console.print(table)
    summary = queue.get_priority_summary()
    console.print("[bold]Priority levels:[/bold]")
    for pri, counts in sorted(summary.items()):
        console.print(f"  Priority {pri}: {counts['total']} jobs")
    console.print()


def _write_patched_workflow(
    workflow_path: Path,
    entry: MatrixEntry,
    image_tag: str | None = None,
    job_id: str | None = None,
    container_mount_options: str | None = None,
    config: LocalCIConfig | None = None,
) -> Path:
    """Write a copy of the workflow with optional container and Codecov patches.

    Patches are applied via :class:`~localci.core.patch_pipeline.PatchPipeline`
    using steps configured in ``.localci.yml`` (``patches:`` section).  All
    patches are text-only (no YAML round-trip).
    """
    cfg = config or LocalCIConfig()
    with open(workflow_path, encoding="utf-8") as f:
        lines = f.readlines()

    ctx = PatchContext(
        lines=lines,
        entry=entry,
        config=cfg,
        image_tag=image_tag,
        job_id=job_id,
        container_mount_options=container_mount_options,
    )
    PatchPipeline.from_config(cfg).apply(ctx)

    fd, path = tempfile.mkstemp(suffix=".yml", prefix="localci-workflow-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(ctx.lines)
    return Path(path)


def make_workflow_patcher(config: LocalCIConfig) -> Callable[..., Path]:
    """Return a workflow patcher callable bound to *config* patch settings."""

    def patcher(
        workflow_path: Path,
        entry: MatrixEntry,
        image_tag: str | None = None,
        job_id: str | None = None,
        container_mount_options: str | None = None,
    ) -> Path:
        return _write_patched_workflow(
            workflow_path,
            entry,
            image_tag=image_tag,
            job_id=job_id,
            container_mount_options=container_mount_options,
            config=config,
        )

    return patcher
