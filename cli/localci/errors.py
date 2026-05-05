"""Structured exception types for LocalCI.

All public errors inherit from :class:`LocalCIError` so callers can catch the
full family with a single ``except LocalCIError`` clause while still being able
to distinguish failure modes precisely.

Hierarchy::

    LocalCIError
    ├── ConfigError
    │   ├── ConfigFileNotFoundError   (also FileNotFoundError)
    │   ├── ConfigIOError             (also OSError)
    │   └── ConfigValidationError
    └── WorkflowError
        ├── WorkflowNotFoundError     (also FileNotFoundError)
        └── WorkflowParseError
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class LocalCIError(Exception):
    """Base exception for all LocalCI errors."""


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


class ConfigError(LocalCIError):
    """Base class for configuration-related errors."""


class ConfigFileNotFoundError(ConfigError, FileNotFoundError):
    """A config file path was given but the file does not exist.

    Inherits from :exc:`FileNotFoundError` for backward-compatibility with
    existing ``except FileNotFoundError`` handlers.

    Attributes
    ----------
    path:
        The config file path that was not found.
    cause:
        The original exception, if any.
    """

    def __init__(self, path: Path, cause: Optional[Exception] = None) -> None:
        self.path = Path(path)
        self.cause = cause
        super().__init__(f"Config file not found: {self.path}")


class ConfigIOError(ConfigError, OSError):
    """The config file exists but could not be read (permission denied, I/O error, etc.).

    Inherits from :exc:`OSError` so existing ``except OSError`` handlers still
    intercept it.

    Attributes
    ----------
    path:
        The config file path that could not be read.
    cause:
        The underlying :exc:`OSError`.
    """

    def __init__(self, path: Path, cause: Exception) -> None:
        self.path = Path(path)
        self.cause = cause
        super().__init__(f"Cannot read config file {self.path}: {cause}")


class ConfigValidationError(ConfigError):
    """The config file content failed schema/Pydantic validation.

    Attributes
    ----------
    path:
        The config file path, or ``None`` when no file was involved.
    cause:
        The underlying :exc:`pydantic.ValidationError` or other validation
        exception carrying the field-level details.
    """

    def __init__(self, path: Optional[Path], cause: Exception) -> None:
        self.path = Path(path) if path is not None else None
        self.cause = cause
        location = f" in {self.path}" if self.path else ""
        super().__init__(f"Invalid config{location}: {cause}")


# ---------------------------------------------------------------------------
# Workflow errors
# ---------------------------------------------------------------------------


class WorkflowError(LocalCIError):
    """Base class for workflow-related errors."""


class WorkflowNotFoundError(WorkflowError, FileNotFoundError):
    """The workflow file does not exist.

    Inherits from :exc:`FileNotFoundError` for backward-compatibility.

    Attributes
    ----------
    path:
        The workflow file path that was not found.
    cause:
        The original exception, if any.
    """

    def __init__(self, path: Path, cause: Optional[Exception] = None) -> None:
        self.path = Path(path)
        self.cause = cause
        super().__init__(f"Workflow file not found: {self.path}")


class WorkflowParseError(WorkflowError):
    """The workflow file could not be parsed (invalid YAML, unexpected structure, etc.).

    Attributes
    ----------
    path:
        Path to the workflow file.
    cause:
        The underlying parse exception.
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = Path(path)
        self.cause = detail
        super().__init__(f"Failed to parse workflow {self.path}: {detail}")
