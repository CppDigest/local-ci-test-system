"""Build a priority queue from workflow analysis and configuration."""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from localci.core.image_tag import derive_image_tag
from localci.core.models import QueuedJob
from localci.core.queue import PriorityConfig, PriorityJobQueue
from localci.core.workflow import MatrixEntry, Platform

if TYPE_CHECKING:
    from localci.core.config import LocalCIConfig
    from localci.core.registry import ImageRegistry
    from localci.core.workflow import Job, Workflow

logger = logging.getLogger(__name__)


def _resolve_image_tag_and_build(
    entry: MatrixEntry,
    registry: Optional["ImageRegistry"],
) -> tuple[Optional[str], Optional[str], bool]:
    """Resolve (image_tag, base_image_tag, needs_build) via registry matching, or derive tag and no build.

    Returns (None, None, True) when no image tag can be derived (e.g. non-Linux runner).
    """
    derived_tag = derive_image_tag(entry)
    if registry is None:
        if derived_tag is not None:
            return derived_tag, None, False
        return None, None, True
    result = registry.select(entry)
    if result.use_image:
        return result.use_image.docker_tag, None, False
    if result.base_image:
        return derived_tag, result.base_image.docker_tag, True
    return derived_tag, None, True


def _matches_filter(entry: MatrixEntry, filters: list[dict]) -> bool:
    """True if entry matches any of the filter dicts."""
    for f in filters:
        match = True
        for key, value in f.items():
            if key == "compiler":
                if entry.compiler.family.value != value:
                    match = False
            elif key == "version":
                if entry.compiler.version != str(value):
                    match = False
            elif key == "name":
                if not fnmatch.fnmatch(entry.name, str(value)):
                    match = False
            elif key == "asan":
                if entry.variant.asan != value:
                    match = False
            elif key == "ubsan":
                if entry.variant.ubsan != value:
                    match = False
            elif key == "coverage":
                if entry.variant.coverage != value:
                    match = False
            elif key == "platform":
                if entry.platform.value != value:
                    match = False
            else:
                # Arbitrary matrix key from workflow (e.g. from --matrix key=value)
                raw_val = entry.raw.get(key)
                if raw_val is None or str(raw_val) != str(value):
                    match = False
        if match:
            return True
    return False


class QueueBuilder:
    """Build a populated PriorityJobQueue from workflow and config.

    Applies platform/compiler/matrix filters, priority assignment, and
    dependency keys. Image matching is stubbed (derive image_tag from
    entry; no registry required until Issue 3).
    """

    def __init__(
        self,
        workflow: "Workflow",
        priority_config: Optional[PriorityConfig] = None,
    ):
        self.workflow = workflow
        self.priority_config = priority_config or PriorityConfig()

    def build(
        self,
        platform_filter: Optional[Platform] = None,
        job_filter: Optional[list[str]] = None,
        compiler_filter: Optional[str] = None,
        matrix_include: Optional[list[dict]] = None,
        matrix_exclude: Optional[list[dict]] = None,
        entries_include: Optional[set[tuple[str, int]]] = None,
        registry_path: Optional[Path] = None,
    ) -> PriorityJobQueue:
        """Build queue. entries_include: when set, only (job_id, entry.index) in this set."""
        queue = PriorityJobQueue()

        # First pass: collect (job, entry) that pass filters and build job_id -> [keys]
        job_keys: dict[str, list[str]] = {}
        candidates: list[tuple["Job", MatrixEntry]] = []

        for job_id, job in self.workflow.jobs.items():
            if job_filter and job_id not in job_filter:
                logger.debug("Skipping job %s (not in filter)", job_id)
                continue
            for entry in job.matrix:
                if entries_include is not None and (job_id, entry.index) not in entries_include:
                    continue
                if platform_filter and entry.platform != platform_filter:
                    continue
                if compiler_filter and entry.compiler.family.value != compiler_filter:
                    continue
                if matrix_exclude and _matches_filter(entry, matrix_exclude):
                    continue
                if matrix_include and not _matches_filter(entry, matrix_include):
                    continue
                key = f"{job_id}:{entry.index}"
                job_keys.setdefault(job_id, []).append(key)
                candidates.append((job, entry))

        # Load registry once so _resolve_image_tag_and_build does not do O(N) disk reads
        registry = None
        if registry_path and registry_path.exists():
            from localci.core.registry import ImageRegistry

            registry = ImageRegistry(registry_path)
            registry.load()

        # Second pass: create QueuedJob with dependency keys, image selection, and priority
        for job, entry in candidates:
            dep_keys = []
            for dep in job.needs:
                dep_keys.extend(job_keys.get(dep, []))
            image_tag, base_image_tag, needs_build = _resolve_image_tag_and_build(
                entry, registry
            )
            
            queued = QueuedJob(
                job_id=job.id,
                matrix_entry=entry,
                priority=0,
                dependencies=dep_keys,
                image_tag=image_tag,
                base_image_tag=base_image_tag,
                needs_build=needs_build,
            )
            queued.priority = self.priority_config.resolve_priority(queued)
            queue.enqueue(queued)

        logger.info(
            "Queue built: %d jobs, %d priority levels",
            queue.total_jobs,
            queue.num_priority_levels,
        )
        return queue
