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
    ├── WorkflowError
    │   ├── WorkflowNotFoundError     (also FileNotFoundError)
    │   ├── WorkflowParseError
    │   ├── MissingFieldError
    │   ├── UnsupportedMatrixError
    │   └── CyclicDependencyError
    ├── ExecutionError                (act / Docker prerequisites)
    │   ├── ActNotFoundError
    │   └── DockerNotAvailableError
    ├── YqError
    └── YqNotFoundError
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


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
        The underlying :exc:`OSError` (read failure) or ``yaml.YAMLError``
        from PyYAML (malformed YAML during load).
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

    Prefer ``raise WorkflowParseError(path, exc) from exc`` so :attr:`cause` and
    :attr:`__cause__` both reference the original exception. Use *message* when
    the user-facing text should add context beyond ``str(exc)``.

    Attributes
    ----------
    path:
        Path to the workflow file.
    cause:
        The underlying exception from parsing or analysis.
    """

    def __init__(
        self,
        path: Path,
        cause: Exception,
        *,
        message: Optional[str] = None,
    ) -> None:
        self.path = Path(path)
        self.cause = cause
        part = message if message is not None else str(cause)
        super().__init__(f"Failed to parse workflow {self.path}: {part}")


class MissingFieldError(WorkflowError):
    """Required field missing from workflow."""

    def __init__(self, field_name: str, context: str) -> None:
        self.field_name = field_name
        self.context = context
        super().__init__(
            f"Missing required field '{field_name}' in {context}"
        )


class UnsupportedMatrixError(WorkflowError):
    """Matrix configuration not supported."""

    def __init__(self, entry: dict[str, Any], detail: str) -> None:
        self.entry = entry
        self.detail = detail
        self.name = entry.get("name", "unknown")
        super().__init__(
            f"Unsupported matrix entry '{self.name}': {detail}"
        )


class CyclicDependencyError(WorkflowError):
    """Circular dependency detected in job graph.

    Attributes
    ----------
    cycle:
        Ordered job IDs forming the loop; first and last elements are equal
        (e.g. ``["a", "b", "a"]``).
    job_id:
        First job in the cycle, kept for backward compatibility (``cycle[0]``).
    """

    def __init__(self, cycle: list[str]) -> None:
        if not isinstance(cycle, list):
            raise TypeError(
                f"cycle must be a list[str], got {type(cycle).__name__!r}; "
                "pass an ordered path such as ['a', 'b', 'a'], not a single job_id string"
            )
        self.cycle = list(cycle)
        self.job_id = self.cycle[0] if self.cycle else ""
        path = " -> ".join(self.cycle)
        super().__init__(f"Cyclic dependency detected: {path}")


# ---------------------------------------------------------------------------
# Execution prerequisites (act, Docker)
# ---------------------------------------------------------------------------


class ExecutionError(LocalCIError):
    """Base class for missing ``act`` or unavailable Docker."""


class ActNotFoundError(ExecutionError):
    """``act`` is not installed."""

    def __init__(self) -> None:
        super().__init__(
            "act is not installed.\n"
            "Install with:\n"
            "  Windows: choco install act-cli\n"
            "  Linux:   curl -s https://raw.githubusercontent.com/nektos/act/"
            "master/install.sh | sudo bash\n"
            "  macOS:   brew install act"
        )


class DockerNotAvailableError(ExecutionError):
    """Docker daemon is not running or not installed."""

    def __init__(self, detail: str = "Docker daemon is not running") -> None:
        super().__init__(detail)


# ---------------------------------------------------------------------------
# YAML query (yq)
# ---------------------------------------------------------------------------


class YqError(LocalCIError):
    """Error from yq execution."""

    def __init__(self, expression: str, stderr: str) -> None:
        self.expression = expression
        self.stderr = stderr
        super().__init__(f"yq error for '{expression}': {stderr}")


class YqNotFoundError(LocalCIError):
    """yq is not installed."""

    def __init__(self) -> None:
        super().__init__(
            "mikefarah/yq is not installed.\n"
            "Install v4+ (not pip's `yq` / kislyuk/yq): "
            "https://github.com/mikefarah/yq#install\n"
            "Prefer a normal binary on PATH (GitHub releases, distro packages). "
            "Snap-installed yq is often confined and cannot read arbitrary paths (e.g. /tmp).\n"
            "Examples:\n"
            "  Windows:  winget install MikeFarah.yq  OR  choco install yq\n"
            "  Linux:    install from https://github.com/mikefarah/yq/releases "
            "OR your distro's `yq` package (ensure `yq --version` shows mikefarah)\n"
            "  macOS:    brew install yq"
        )
