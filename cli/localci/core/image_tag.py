"""Shared logic for deriving Docker image tags from matrix entries."""

from __future__ import annotations

from localci.core.workflow import MatrixEntry


def _is_linux_runner(runs_on: str) -> bool:
    """True if runs_on identifies a Linux runner (e.g. linux, linux-latest, ubuntu-24.04)."""
    r = runs_on.strip().lower()
    return r.startswith("linux") or "ubuntu" in r


def derive_image_tag(entry: MatrixEntry) -> str | None:
    """Derive a Docker image tag from a matrix entry.

    Uses capy image names (e.g. capy-ubuntu-24.04-clang20-x86:latest).
    Only builds a synthesized capy tag when:
    - entry.container.image is present (normalized img/os_label behavior), or
    - entry.runs_on is a Linux runner (e.g. startswith "linux" or project Linux identifiers).
    When container.image is absent and runs_on is not Linux, returns None so callers
    do not add invalid container mappings.
    """
    if entry.container.image:
        img = entry.container.image.strip().lower()
        os_label = img.replace(":", "-", 1) if ":" in img else img
    else:
        if not _is_linux_runner(entry.runs_on):
            return None
        os_label = entry.runs_on
    compiler_label = f"{entry.compiler.family.value}{entry.compiler.version}"
    base = f"capy-{os_label}-{compiler_label}"
    if entry.variant.coverage:
        base += "-cov"
    elif entry.variant.asan:
        base += "-asan"
    elif entry.variant.x86:
        base += "-x86"
    return f"{base}:latest"
