"""Error classes for workflow analysis.

Provides structured exceptions for common failure modes during
workflow parsing, matrix resolution, and field extraction.
"""

from __future__ import annotations

from pathlib import Path


class WorkflowError(Exception):
    """Base error for workflow analysis."""


class WorkflowParseError(WorkflowError):
    """Failed to parse workflow YAML."""

    def __init__(self, file: Path, detail: str):
        super().__init__(f"Failed to parse {file}: {detail}")


class UnsupportedMatrixError(WorkflowError):
    """Matrix configuration not supported."""

    def __init__(self, entry: dict, detail: str):
        name = entry.get("name", "unknown")
        super().__init__(f"Unsupported matrix entry '{name}': {detail}")


class MissingFieldError(WorkflowError):
    """Required field missing from workflow."""

    def __init__(self, field_name: str, context: str):
        super().__init__(f"Missing required field '{field_name}' in {context}")
