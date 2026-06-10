"""Boost dependency cache: pre-clone/update so jobs can use a shared Boost tree.

Phase 2 (Issue 10): avoid cloning Boost on every run by maintaining a
host-side cache and bind-mounting it into containers.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from localci.core.config import CacheConfig

logger = logging.getLogger(__name__)

# Default Boost superproject URL (used when cache.boost.remote is not set)
DEFAULT_BOOST_REPO_URL = "https://github.com/boostorg/boost.git"


def ensure_boost_cache(
    cache_config: CacheConfig,
    no_cache: bool,
    cache_dir_override: Path | None = None,
) -> bool:
    """Ensure the Boost cache directory exists and is a git repo (clone or fetch).

    If cache is disabled (no_cache or cache.enabled/boost.enabled false), returns True
    (nothing to do). Otherwise resolves the boost cache dir; if missing or not a git
    repo, runs ``git clone`` (shallow when config.boost.shallow); if already a repo,
    runs ``git fetch`` to update.

    Returns True on success, False on failure.
    """
    if no_cache or not cache_config.enabled or not cache_config.boost.enabled:
        return True
    root = cache_dir_override or cache_config.directory
    root = Path(root).expanduser().resolve()
    boost_dir = cache_config.boost.dir or root / "boost"
    boost_dir = Path(boost_dir).expanduser().resolve()
    branch = cache_config.boost.branch
    shallow = getattr(cache_config.boost, "shallow", True)

    remote_url = cache_config.boost.remote or DEFAULT_BOOST_REPO_URL

    if not boost_dir.exists():
        boost_dir.parent.mkdir(parents=True, exist_ok=True)
        return _git_clone(boost_dir, branch, shallow, remote_url)

    if not (boost_dir / ".git").is_dir():
        logger.debug(
            "Boost cache path %s exists but is not a git repo; skipping bootstrap",
            boost_dir,
        )
        return True

    return _git_fetch_and_update(boost_dir, branch, shallow)


def _git_clone(dest: Path, branch: str, shallow: bool, remote_url: str) -> bool:
    """Clone Boost repo. Returns True on success, False on failure."""
    args = ["git", "clone", "--branch", branch]
    if shallow:
        args.extend(["--depth", "1"])
    args.extend([remote_url, str(dest)])
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=300)
        logger.info("Boost cache cloned at %s (branch=%s)", dest, branch)
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as e:
        logger.warning(
            "Boost cache clone failed: %s (stderr: %s)",
            e,
            getattr(e, "stderr", "") or "(none)",
        )
        return False


def _git_fetch_and_update(dest: Path, branch: str, shallow: bool = False) -> bool:
    """Fetch origin and reset working tree to origin/<branch>. Returns True on success."""
    try:
        fetch_args = ["git", "-C", str(dest), "fetch", "origin", branch]
        if shallow:
            fetch_args.extend(["--depth", "1"])
        subprocess.run(
            fetch_args,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        logger.debug("Boost cache updated at %s (branch=%s)", dest, branch)
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as e:
        logger.warning(
            "Boost cache fetch/update failed: %s (stderr: %s)",
            e,
            getattr(e, "stderr", "") or "(none)",
        )
        return False
