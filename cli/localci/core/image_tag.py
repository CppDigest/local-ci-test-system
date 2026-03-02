"""Shared logic for deriving Docker image tags from matrix entries."""

from __future__ import annotations

from localci.core.workflow import MatrixEntry


def derive_image_tag(entry: MatrixEntry) -> str | None:
    """Derive a Docker image tag from a matrix entry.

    Uses capy image names (e.g. capy-ubuntu-24.04-clang20-x86:latest).
    Uses container image or runs_on for the OS label (e.g. ubuntu:24.04 -> ubuntu-24.04).
    Returns None for non-Linux platforms (windows, macos, or runs_on not ubuntu/linux)
    when container.image is empty, so callers do not add invalid capy image mappings.
    """
    if entry.container.image:
        img = entry.container.image.strip().lower()
        if ":" in img:
            os_label = img.replace(":", "-", 1)
        else:
            os_label = img
    else:
        runs_on = entry.runs_on
        if runs_on.startswith("windows") or runs_on.startswith("macos"):
            return None
        if "ubuntu" not in runs_on.lower() and runs_on != "linux":
            return None
        os_label = runs_on
    compiler_label = (
        f"{entry.compiler.family.value}{entry.compiler.version}"
    )
    base = f"capy-{os_label}-{compiler_label}"
    if entry.variant.coverage:
        base += "-cov"
    elif entry.variant.asan:
        base += "-asan"
    elif entry.variant.x86:
        base += "-x86"
    return f"{base}:latest"
