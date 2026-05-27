"""Crash log utilities for unhandled CLI exceptions."""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from localci import __version__
from localci.utils.paths import localci_home

CRASH_LOG_NAME = "crash.log"


def crash_log_path() -> Path:
    """Return the path to the crash log file (``~/.localci/crash.log``)."""
    return localci_home() / CRASH_LOG_NAME


def log_crash(exc: BaseException) -> Path:
    """Write a full crash report for *exc* and return the log file path."""
    path = crash_log_path()
    lines = [
        f"timestamp: {datetime.now(timezone.utc).isoformat()}\n",
        f"localci_version: {__version__}\n",
        f"python_version: {sys.version}\n",
        f"platform: {platform.platform()}\n",
        "\n",
        "traceback:\n",
        *traceback.format_exception(type(exc), exc, exc.__traceback__),
    ]
    path.write_text("".join(lines), encoding="utf-8")
    return path
