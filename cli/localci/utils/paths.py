"""Path utilities for Local CI.

Centralises common path resolution logic so that individual commands
don't have to duplicate it.
"""

from __future__ import annotations

from pathlib import Path


def resolve_workflow(
    path: str | Path | None, config_workflow: Path | None = None
) -> Path:
    """Resolve a workflow file path.

    Priority:
    1. Explicit *path* argument from the CLI
    2. ``workflow`` value from the loaded config
    3. Default ``.github/workflows/ci.yml``

    Raises
    ------
    FileNotFoundError
        If the resolved path does not exist.
    """
    if path is not None:
        resolved = Path(path).resolve()
    elif config_workflow is not None:
        resolved = Path(config_workflow).resolve()
    else:
        resolved = Path(".github/workflows/ci.yml").resolve()

    if not resolved.is_file():
        raise FileNotFoundError(f"Workflow file not found: {resolved}")

    return resolved


def ensure_directory(path: Path) -> Path:
    """Create directory (and parents) if it does not exist, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def localci_home() -> Path:
    """Return the root data directory for localci (``~/.localci``)."""
    return ensure_directory(Path.home() / ".localci")
