"""CLI option bundle for ``localci run``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunOptions:
    """Normalized options passed from the Click entry point to orchestration."""

    workflow: str | None
    jobs: tuple[str, ...]
    platform: str | None
    compiler: str | None
    matrix_filters: tuple[str, ...]
    parallel: int | None
    timeout: int | None
    dry_run: bool
    no_cache: bool
    cache_dir: Path | None
    rebuild_image: bool
    keep_containers: bool | None
    interactive: bool
    verbose: bool
    github_token: str | None
    offline: bool
