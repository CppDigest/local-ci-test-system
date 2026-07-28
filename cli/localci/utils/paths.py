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


REGISTRY_FILENAME = "image-registry.yml"
IMAGES_CAPY_REL = Path("images") / "capy"


def find_file_upward(filename: str, start_dir: Path) -> Path | None:
    """Return the first *filename* found walking up from *start_dir*."""
    directory = start_dir.resolve()
    while True:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def resolve_registry_path(
    registry_path: Path | None = None,
    *,
    start_dir: Path | None = None,
    module_file: Path | None = None,
) -> Path:
    """Resolve the image registry file for ``localci images`` commands.

    Priority:
    1. Explicit *registry_path* (e.g. ``--registry``)
    2. Walk upward from *start_dir* or the current working directory
    3. Walk upward from *module_file*'s directory (editable/source installs)

    Image assets are not bundled in the installed wheel. If the working
    directory has no ``image-registry.yml`` ancestor and the installed package
    path (for example ``site-packages``) is not under a checkout that contains
    the registry, resolution fails unless ``--registry`` is provided.

    Raises
    ------
    FileNotFoundError
        If no registry file can be located.
    """
    if registry_path is not None:
        resolved = Path(registry_path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Registry file not found: {resolved}")
        return resolved

    found = find_file_upward(REGISTRY_FILENAME, start_dir or Path.cwd())
    if found is None and module_file is not None:
        found = find_file_upward(REGISTRY_FILENAME, Path(module_file).resolve().parent)

    if found is None:
        origin = start_dir or Path.cwd()
        raise FileNotFoundError(
            f"Registry file not found: {REGISTRY_FILENAME} "
            f"(searched upward from {origin} and the installed package; "
            f"use --registry or run from a directory containing {REGISTRY_FILENAME})"
        )
    return found


def resolve_images_dir(registry_path: Path) -> Path:
    """Return ``images/capy`` beside the resolved registry file."""
    return registry_path.resolve().parent / IMAGES_CAPY_REL
