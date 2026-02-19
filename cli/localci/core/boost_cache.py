"""Boost dependency cache: pre-clone/update so jobs can use a shared Boost tree.

Phase 2 (Issue 10): avoid cloning Boost on every run by maintaining a
host-side cache and bind-mounting it into containers.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from localci.core.config import CacheConfig

logger = logging.getLogger(__name__)

# Default Boost superproject URL (used when cache.boost.remote is not set)
DEFAULT_BOOST_REPO_URL = "https://github.com/boostorg/boost.git"


def ensure_boost_cache(
    cache_config: "CacheConfig",
    no_cache: bool,
    cache_dir_override: Optional[Path] = None,
) -> None:
    """Ensure the Boost cache directory exists and is a git repo (clone or fetch).

    If cache is disabled (no_cache or cache.enabled/boost.enabled false), returns.
    Otherwise resolves the boost cache dir; if missing or not a git repo, runs
    ``git clone`` (shallow when config.boost.shallow); if already a repo, runs
    ``git fetch`` to update.
    """
    if no_cache or not cache_config.enabled or not cache_config.boost.enabled:
        return
    root = cache_dir_override or cache_config.directory
    root = Path(root).expanduser().resolve()
    boost_dir = cache_config.boost.dir or root / "boost"
    boost_dir = Path(boost_dir).expanduser().resolve()
    branch = cache_config.boost.branch
    shallow = getattr(cache_config.boost, "shallow", True)

    remote_url = cache_config.boost.remote or DEFAULT_BOOST_REPO_URL

    if not boost_dir.exists():
        boost_dir.parent.mkdir(parents=True, exist_ok=True)
        _git_clone(boost_dir, branch, shallow, remote_url)
        return

    if not (boost_dir / ".git").is_dir():
        logger.debug(
            "Boost cache path %s exists but is not a git repo; skipping bootstrap",
            boost_dir,
        )
        return

    _git_fetch_and_update(boost_dir, branch, shallow)


def _git_clone(dest: Path, branch: str, shallow: bool, remote_url: str) -> None:
    args = ["git", "clone", "--branch", branch]
    if shallow:
        args.extend(["--depth", "1"])
    args.extend([remote_url, str(dest)])
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
        logger.info("Boost cache cloned at %s (branch=%s)", dest, branch)
    except subprocess.CalledProcessError as e:
        logger.warning(
            "Boost cache clone failed: %s (stderr: %s)",
            e,
            (e.stderr or "").strip() or "(none)",
        )


def _git_fetch_and_update(dest: Path, branch: str, shallow: bool = False) -> None:
    """Fetch origin and reset working tree to origin/<branch>."""
    try:
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "origin", branch],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.debug("Boost cache updated at %s (branch=%s)", dest, branch)
    except subprocess.CalledProcessError as e:
        logger.debug(
            "Boost cache fetch/update failed (non-fatal): %s",
            (e.stderr or "").strip() or str(e),
        )
