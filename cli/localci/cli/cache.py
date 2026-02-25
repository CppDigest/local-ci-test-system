"""``localci cache`` command group (Issue 9: clear, stats)."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from localci.core.boost_cache import ensure_boost_cache
from localci.core.ccache_stats import get_ccache_stats
from localci.core.config import resolve_cache_paths
from localci.utils.output import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)


@click.group()
@click.pass_context
def cache_cmd(ctx: click.Context) -> None:
    """Manage build caches (ccache, boost, cmake)."""


# ---------------------------------------------------------------------------
# localci cache clear
# ---------------------------------------------------------------------------


@cache_cmd.command("clear")
@click.option(
    "--target",
    "-t",
    type=click.Choice(["ccache", "boost", "cmake", "b2-source", "apt", "all"]),
    default="ccache",
    help="Which cache to clear (default: ccache).",
)
@click.option(
    "--yes",
    "-y",
    "confirm",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
@click.pass_context
def cache_clear(
    ctx: click.Context,
    target: str,
    confirm: bool,
) -> None:
    """Remove cache directories to force fresh builds.

    Use after changing compiler/toolchain or to free disk space.
    """
    cfg = ctx.obj["config"]
    if not cfg.cache.enabled:
        print_warning("Cache is disabled in config; nothing to clear.")
        return

    root = Path(cfg.cache.directory).expanduser().resolve()
    dirs_to_remove: list[Path] = []

    if target in ("ccache", "all") and cfg.cache.ccache.enabled:
        d = cfg.cache.ccache.dir or root / "ccache"
        dirs_to_remove.append(Path(d).expanduser().resolve())
    if target in ("boost", "all") and cfg.cache.boost.enabled:
        d = cfg.cache.boost.dir or root / "boost"
        dirs_to_remove.append(Path(d).expanduser().resolve())
    if target in ("cmake", "all") and cfg.cache.cmake.enabled:
        d = cfg.cache.cmake.dir or root / "cmake"
        dirs_to_remove.append(Path(d).expanduser().resolve())
    if target in ("b2-source", "all") and cfg.cache.boost.enabled and getattr(
        cfg.cache.boost, "build_dir", True
    ):
        d = root / "b2-source"
    if target in ("apt", "all") and cfg.cache.apt.enabled:
        d = cfg.cache.apt.dir or root / "apt"
        dirs_to_remove.append(Path(d).expanduser().resolve())

    if not dirs_to_remove:
        print_info("No cache directories configured for the selected target.")
        return

    if not confirm:
        for p in dirs_to_remove:
            console.print(f"  {p}")
        click.confirm(
            f"Remove {len(dirs_to_remove)} cache directory/ies above?",
            default=False,
            abort=True,
        )

    for p in dirs_to_remove:
        if not p.exists():
            print_info(f"Skip (not found): {p}")
            continue
        try:
            shutil.rmtree(p)
            print_success(f"Cleared: {p}")
        except OSError as e:
            print_error(f"Failed to remove {p}: {e}")
            raise click.Abort() from e


# ---------------------------------------------------------------------------
# localci cache stats
# ---------------------------------------------------------------------------


@cache_cmd.command("stats")
@click.pass_context
def cache_stats(ctx: click.Context) -> None:
    """Show ccache statistics (hit/miss, size) for the configured cache dir."""
    cfg = ctx.obj["config"]
    if not cfg.cache.enabled or not cfg.cache.ccache.enabled:
        print_warning("ccache is disabled in config.")
        return

    resolved = resolve_cache_paths(cfg.cache, False, None, None, None)
    if not resolved or resolved.ccache_host is None:
        print_warning("Could not resolve ccache path.")
        return

    if not resolved.ccache_host.exists():
        print_info(f"ccache directory does not exist yet: {resolved.ccache_host}")
        print_info("Run a build with cache enabled to populate it.")
        return

    stats = get_ccache_stats(resolved.ccache_host)
    if not stats:
        print_warning(
            "Could not run ccache -s (ccache may not be installed on host, "
            "or directory is not a ccache cache)."
        )
        return

    print_info(f"ccache stats ({resolved.ccache_host}):")
    for line in stats.splitlines():
        console.print(f"  {line}")


# ---------------------------------------------------------------------------
# localci cache update (Issue 10: refresh Boost cache outside a run)
# ---------------------------------------------------------------------------


@cache_cmd.command("update")
@click.option(
    "--target",
    "-t",
    type=click.Choice(["boost"]),
    default="boost",
    help="Which cache to update (default: boost).",
)
@click.pass_context
def cache_update(ctx: click.Context, target: str) -> None:
    """Refresh cache from remote (e.g. git fetch + checkout for Boost).

    Use to update the Boost superproject cache without running a full CI run.
    """
    cfg = ctx.obj["config"]
    if not cfg.cache.enabled:
        print_warning("Cache is disabled in config.")
        return

    if target == "boost":
        if not cfg.cache.boost.enabled:
            print_warning("Boost cache is disabled in config.")
            return
        print_info("Updating Boost cache (clone or fetch + reset)...")
        ensure_boost_cache(cfg.cache, no_cache=False, cache_dir_override=None)
        print_success("Boost cache update complete.")
