"""CMake configuration cache: change detection and path keying (Issue 11).

Computes an input digest from CMake inputs (files, compiler, BOOST_ROOT) so
that the cache path is keyed by configuration; when inputs change, a new path
is used and CMake reconfigures.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from localci.core.config import LOCALCI_CACHE_CONTAINER_ROOT

if TYPE_CHECKING:
    from localci.core.config import CmakeCacheConfig
    from localci.core.workflow import MatrixEntry

logger = logging.getLogger(__name__)

# Default paths/globs (relative to project root) included in change detection
DEFAULT_CMAKE_INPUTS = ["CMakeLists.txt", "cmake/*.cmake"]

# Must match ResolvedCachePaths.boost_container so digest and runtime BOOST_ROOT stay in sync
def _boost_container_path_for_digest() -> str:
    return f"{LOCALCI_CACHE_CONTAINER_ROOT}/boost"


def compute_cmake_input_digest(
    project_dir: Path,
    entry: "MatrixEntry",
    cmake_config: "CmakeCacheConfig",
    boost_enabled: bool,
) -> str:
    """Compute a short digest of inputs that affect CMake configuration.

    Used to key the CMake cache directory: same digest => reuse cache; different
    digest => new directory and reconfigure. Includes:
    - File contents from cache.cmake.inputs or default (CMakeLists.txt, cmake/*.cmake)
    - Compiler (CC, CXX from matrix entry)
    - BOOST_ROOT (fixed value when boost cache enabled)

    Returns a 12-character hex string.
    """
    project_dir = Path(project_dir).resolve()
    h = hashlib.sha256()

    # File inputs
    input_specs = cmake_config.inputs if cmake_config.inputs else DEFAULT_CMAKE_INPUTS
    for spec in sorted(input_specs):
        if "*" in spec:
            # Glob
            for p in sorted(project_dir.glob(spec)):
                if p.is_file():
                    _hash_file(h, p, project_dir)
        else:
            p = project_dir / spec
            if p.is_file():
                _hash_file(h, p, project_dir)

    # Compiler (affects CMake toolchain detection)
    cc = (entry.compiler.cc or "").strip()
    cxx = (entry.compiler.cxx or "").strip()
    h.update(f"CC={cc}\nCXX={cxx}\n".encode())

    # BOOST_ROOT (same as ResolvedCachePaths.boost_container so digest matches runtime)
    if boost_enabled:
        h.update(f"BOOST_ROOT={_boost_container_path_for_digest()}\n".encode())

    return h.hexdigest()[:12]


def _hash_file(h: Any, path: Path, project_dir: Path) -> None:
    """Update hash with relative path and file content."""
    try:
        rel = path.relative_to(project_dir)
    except ValueError:
        rel = path
    h.update(f"file:{rel}\n".encode())
    try:
        h.update(path.read_bytes())
    except OSError as e:
        logger.debug("Could not read %s for CMake digest: %s", path, e)
    h.update(b"\n")
