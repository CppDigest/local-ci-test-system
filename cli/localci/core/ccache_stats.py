"""ccache statistics: run ccache -s for a cache dir (Issue 9 reporting)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_ccache_stats(host_ccache_dir: Path) -> Optional[str]:
    """Run ``ccache -s`` for the given host cache directory.

    Uses CCACHE_DIR so stats reflect the cache used by jobs. Returns the
    stdout of ``ccache -s``, or None if ccache is not installed or the
    command fails (e.g. dir missing or not a ccache dir).
    """
    ccache = shutil.which("ccache")
    if not ccache:
        return None
    if not host_ccache_dir.exists():
        return None
    try:
        result = subprocess.run(
            [ccache, "-s"],
            env={"CCACHE_DIR": str(host_ccache_dir)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.debug("ccache -s failed: %s", result.stderr or result.stdout)
            return None
        return result.stdout.strip() if result.stdout else None
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ccache -s error: %s", e)
        return None
