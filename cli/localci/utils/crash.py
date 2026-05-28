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


def crash_log_display_path() -> str:
    """Return a short user-facing path for the crash log (e.g. ``~/.localci/crash.log``)."""
    path = crash_log_path()
    try:
        rel = path.relative_to(Path.home())
        return "~/" + rel.as_posix()
    except ValueError:
        return str(path)


def _build_crash_report(exc: BaseException) -> str:
    return "".join(
        [
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n",
            f"localci_version: {__version__}\n",
            f"python_version: {sys.version}\n",
            f"platform: {platform.platform()}\n",
            "\n",
            "traceback:\n",
            *traceback.format_exception(type(exc), exc, exc.__traceback__),
        ]
    )


def log_crash(exc: BaseException) -> Path | None:
    """Write a full crash report for *exc* and return the log file path.

    On write failure, prints the report to stderr and returns ``None``.
    """
    path = crash_log_path()
    report = _build_crash_report(exc)
    try:
        path.write_text(report, encoding="utf-8")
        return path
    except OSError:
        sys.stderr.write(f"Could not write crash log to {path}:\n")
        sys.stderr.write(report)
        return None
