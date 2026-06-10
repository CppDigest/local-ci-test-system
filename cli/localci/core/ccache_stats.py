"""ccache statistics: run ccache -s for a cache dir (Issue 9 reporting)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_ccache_stats(host_ccache_dir: Path) -> str | None:
    """Run ``ccache -s`` for the given host cache directory.

    Uses CCACHE_DIR so stats reflect the cache used by jobs. Returns the
    stdout of ``ccache -s``, or None if ccache is not installed or the
    command fails (e.g. dir missing or not a ccache dir).
    """
    ccache = shutil.which("ccache")
    if not ccache:
        return None
    if not host_ccache_dir.is_dir():
        return None
    try:
        env = os.environ.copy()
        env["CCACHE_DIR"] = str(host_ccache_dir)
        result = subprocess.run(
            [ccache, "-s"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(
                "ccache -s failed (returncode=%s): stderr=%r stdout=%r",
                result.returncode,
                result.stderr,
                result.stdout,
            )
            return None
        return result.stdout.strip() if result.stdout else None
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ccache -s error: %s", e)
        return None
