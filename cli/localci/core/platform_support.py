"""Platform enablement and unsupported-platform messaging for local CI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from localci.core.models import PlatformOutcome
from localci.core.workflow import MatrixEntry, Platform

if TYPE_CHECKING:
    from localci.core.config import PlatformConfig


def resolve_platform_outcome(
    entry: MatrixEntry,
    config: PlatformConfig,
) -> PlatformOutcome:
    """Resolve whether a matrix entry should run, fail, or skip.

    Windows and macOS jobs are not runnable under act on Linux. With the
    platform flag off (default), they fail loud. Setting
    ``platforms.windows`` or ``platforms.macos`` to ``true`` opts in to
    skipping those jobs without failing the overall run.
    """
    platform = entry.platform
    if platform == Platform.LINUX:
        return PlatformOutcome.RUN if config.linux else PlatformOutcome.SKIP
    if platform == Platform.WINDOWS:
        return PlatformOutcome.SKIP if config.windows else PlatformOutcome.FAIL
    if platform == Platform.MACOS:
        return PlatformOutcome.SKIP if config.macos else PlatformOutcome.FAIL
    return PlatformOutcome.FAIL


def unsupported_platform_message(job_id: str, entry: MatrixEntry) -> str:
    """Explicit failure message for a non-Linux matrix entry."""
    platform = entry.platform.value
    return (
        f"Job '{job_id}' / '{entry.name}' uses platform '{platform}' "
        f"(runs-on: {entry.runs_on}). Local CI supports Linux jobs only. "
        f"Set platforms.{platform}: true in .localci.yml to skip {platform} "
        f"jobs without failing the run, or use --platform linux to run only "
        f"Linux matrix entries."
    )


def skipped_platform_message(job_id: str, entry: MatrixEntry) -> str:
    """Message when an unsupported platform job is skipped via config."""
    platform = entry.platform.value
    return (
        f"Skipped job '{job_id}' / '{entry.name}': platform '{platform}' "
        f"(runs-on: {entry.runs_on}) is not supported by local CI (Linux-only; "
        f"platforms.{platform}: true)."
    )
