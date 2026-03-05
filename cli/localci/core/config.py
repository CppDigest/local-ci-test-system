"""Configuration models for Local CI.

Handles loading, validating, and merging configuration from .localci.yml files.
Uses Pydantic v2 for type-safe validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


def _expand_path(v: Path) -> Path:
    """Expand ~ and resolve to an absolute path."""
    return Path(v).expanduser().resolve()


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ResourceLimitConfig(BaseModel):
    """Resource limits for parallel execution."""

    cpu_percent: int = Field(default=80, ge=1, le=100)
    memory_percent: int = Field(default=70, ge=1, le=100)


class ParallelConfig(BaseModel):
    """Parallelism settings."""

    max_jobs: int = Field(default=8, ge=1, le=64)
    resource_limit: ResourceLimitConfig = Field(
        default_factory=ResourceLimitConfig,
    )


class PlatformConfig(BaseModel):
    """Which platforms to run locally."""

    linux: bool = True
    windows: bool = False
    macos: bool = False


class JobsConfig(BaseModel):
    """Job include/exclude filters."""

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MatrixFilter(BaseModel):
    """Single matrix filter entry."""

    compiler: Optional[str] = None
    version: Optional[str] = None
    name: Optional[str] = None
    asan: Optional[bool] = None
    ubsan: Optional[bool] = None


class MatrixConfig(BaseModel):
    """Matrix include/exclude filters."""

    include: list[MatrixFilter] = Field(default_factory=list)
    exclude: list[MatrixFilter] = Field(default_factory=list)


class ImageCleanupConfig(BaseModel):
    """Image cleanup settings."""

    enabled: bool = True
    max_age_days: int = Field(default=30, ge=1)
    max_size_gb: int = Field(default=20, ge=1)


class ImagesConfig(BaseModel):
    """Docker image management settings."""

    registry: Path = Field(default_factory=lambda: Path.home() / ".localci" / "images")
    auto_build: bool = True

    @field_validator("registry", mode="after")
    @classmethod
    def expand_registry(cls, v: Path) -> Path:
        return _expand_path(v)
    cleanup: ImageCleanupConfig = Field(default_factory=ImageCleanupConfig)


class CcacheConfig(BaseModel):
    """ccache settings."""

    enabled: bool = True
    max_size: str = "5G"
    compress: bool = True  # CCACHE_COMPRESS
    dir: Optional[Path] = None  # default: cache.directory / "ccache"


class BoostCacheConfig(BaseModel):
    """Boost dependency cache settings."""

    enabled: bool = True
    branch: str = "develop"
    dir: Optional[Path] = None  # default: cache.directory / "boost"
    shallow: bool = True
    remote: Optional[str] = None  # default: https://github.com/boostorg/boost.git
    # When True, cache b2 build artifacts (bin.v2) per job so b2 does incremental builds
    build_dir: bool = True


class CmakeCacheConfig(BaseModel):
    """CMake configuration cache settings (per job/matrix)."""

    enabled: bool = True
    dir: Optional[Path] = None  # base dir; per-job path is dir / <job_matrix_key>[_<input_digest>]
    # Optional: paths/globs relative to project root included in change detection (default: CMakeLists.txt, cmake/*.cmake)
    inputs: Optional[list[str]] = None


class CacheConfig(BaseModel):
    """Build caching settings."""

    enabled: bool = True
    directory: Path = Field(default_factory=lambda: Path.home() / ".localci" / "cache")
    ccache: CcacheConfig = Field(default_factory=CcacheConfig)
    boost: BoostCacheConfig = Field(default_factory=BoostCacheConfig)
    cmake: CmakeCacheConfig = Field(default_factory=CmakeCacheConfig)

    @field_validator("directory", mode="after")
    @classmethod
    def expand_directory(cls, v: Path) -> Path:
        return _expand_path(v)


class LoggingConfig(BaseModel):
    """Logging settings."""

    level: str = "info"
    directory: Path = Field(default_factory=lambda: Path.home() / ".localci" / "logs")
    max_files: int = Field(default=10, ge=1)
    max_size_mb: int = Field(default=100, ge=1)

    @field_validator("directory", mode="after")
    @classmethod
    def expand_directory(cls, v: Path) -> Path:
        """Expand ~ so all consumers get an absolute path."""
        return _expand_path(v)


class ExecutionConfig(BaseModel):
    """Execution behaviour settings."""

    timeout: int = Field(default=3600, ge=1)
    keep_containers: bool = False
    stop_on_first_failure: bool = False


# ---------------------------------------------------------------------------
# Root configuration model
# ---------------------------------------------------------------------------


class LocalCIConfig(BaseModel):
    """Root configuration model for .localci.yml."""

    version: int = 1
    workflow: Path = Field(default=Path(".github/workflows/ci.yml"))
    event: str = "push"

    @field_validator("workflow", mode="after")
    @classmethod
    def expand_workflow(cls, v: Path) -> Path:
        return _expand_path(v)

    parallel: ParallelConfig = Field(default_factory=ParallelConfig)
    platforms: PlatformConfig = Field(default_factory=PlatformConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)
    priorities: dict[str, int] = Field(default_factory=dict)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


# ---------------------------------------------------------------------------
# Cache path resolution (Phase 2)
# ---------------------------------------------------------------------------

# Container paths used when bind-mounting host cache dirs (act --container-options -v ...)
LOCALCI_CACHE_CONTAINER_ROOT = "/tmp/localci-cache"


@dataclass
class ResolvedCachePaths:
    """Resolved host paths and container paths for Phase 2 caches."""

    ccache_host: Optional[Path] = None
    boost_host: Optional[Path] = None
    cmake_host: Optional[Path] = None  # per-job: cache_base / job_id / matrix_key
    b2_source_host: Optional[Path] = None  # per-job: persistent boost-root (source + bin.v2 artifacts)

    @property
    def ccache_container(self) -> str:
        return f"{LOCALCI_CACHE_CONTAINER_ROOT}/ccache"

    @property
    def boost_container(self) -> str:
        return f"{LOCALCI_CACHE_CONTAINER_ROOT}/boost"

    @property
    def cmake_container(self) -> str:
        return f"{LOCALCI_CACHE_CONTAINER_ROOT}/cmake"

    @property
    def b2_source_container(self) -> str:
        return f"{LOCALCI_CACHE_CONTAINER_ROOT}/b2-source"

    def host_dirs_to_ensure(self) -> list[Path]:
        """Host directories that must exist before bind-mounting."""
        out: list[Path] = []
        if self.ccache_host is not None:
            out.append(self.ccache_host)
        if self.boost_host is not None:
            out.append(self.boost_host)
        if self.cmake_host is not None:
            out.append(self.cmake_host)
        if self.b2_source_host is not None:
            out.append(self.b2_source_host)
        return out


def resolve_cache_paths(
    cache_config: CacheConfig,
    no_cache: bool,
    cache_dir_override: Optional[Path] = None,
    job_id: Optional[str] = None,
    queue_key: Optional[str] = None,
    cmake_input_digest: Optional[str] = None,
) -> Optional[ResolvedCachePaths]:
    """Resolve host cache paths for use with act bind mounts.

    Returns None if caching is disabled (no_cache, or cache.enabled or
    per-cache enabled flags false). Otherwise returns resolved paths;
    paths are expanded (expanduser) and resolved to absolute.

    When *cmake_input_digest* is provided and CMake cache is enabled, the
    CMake cache path is keyed by job/matrix and digest so that different
    inputs (CMakeLists.txt, toolchain, compiler, BOOST_ROOT) get different
    directories (Issue 11 change detection).
    """
    if no_cache or not cache_config.enabled:
        return None
    root = cache_dir_override or cache_config.directory
    root = Path(root).expanduser().resolve()

    r = ResolvedCachePaths()
    if cache_config.ccache.enabled:
        d = cache_config.ccache.dir or root / "ccache"
        r.ccache_host = Path(d).expanduser().resolve()
    if cache_config.boost.enabled:
        d = cache_config.boost.dir or root / "boost"
        r.boost_host = Path(d).expanduser().resolve()
        if getattr(cache_config.boost, "build_dir", True) and job_id and queue_key:
            b2_base = root / "b2-source"
            safe_key = queue_key.replace(":", "-")
            r.b2_source_host = Path(b2_base).expanduser().resolve() / safe_key
    if cache_config.cmake.enabled and job_id and queue_key:
        base = cache_config.cmake.dir or root / "cmake"
        base = Path(base).expanduser().resolve()
        safe_key = queue_key.replace(":", "-")
        if cmake_input_digest:
            subdir = f"{safe_key}_{cmake_input_digest}"
        else:
            subdir = safe_key
        r.cmake_host = base / subdir

    if (
        r.ccache_host is None
        and r.boost_host is None
        and r.cmake_host is None
        and r.b2_source_host is None
    ):
        return None
    return r


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

CONFIG_FILENAMES = [".localci.yml", ".localci.yaml", "localci.yml"]


def find_config_file(start_dir: Path | None = None) -> Path | None:
    """Search for a config file starting from *start_dir* up to the filesystem root.

    Returns the first matching path, or ``None`` if no config is found.
    """
    directory = (start_dir or Path.cwd()).resolve()

    while True:
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        parent = directory.parent
        if parent == directory:
            break
        directory = parent

    return None


def load_config(path: Path | str | None = None) -> LocalCIConfig:
    """Load and validate a Local CI configuration.

    Parameters
    ----------
    path:
        Explicit path to the config file.  When *None*, the config file is
        discovered automatically via :func:`find_config_file`.

    Returns
    -------
    LocalCIConfig
        A validated configuration object.  If no config file is found,
        returns a default configuration.
    """
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Config file not found: {config_path}")
    else:
        config_path = find_config_file()

    if config_path is None:
        logger.debug("No config file found, using defaults")
        return LocalCIConfig()

    logger.debug("Loading config from %s", config_path)
    with open(config_path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    return LocalCIConfig.model_validate(raw)


def default_config_yaml() -> str:
    """Return the default configuration as a YAML string.

    Useful for ``localci config init``.
    """
    cfg = LocalCIConfig()
    data = cfg.model_dump(mode="json")
    # Convert Path objects to strings for YAML serialisation
    _stringify_paths(data)
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def _stringify_paths(obj: Any) -> None:
    """Recursively convert Path-like values to strings inside nested dicts."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, Path):
                obj[key] = str(value)
            elif isinstance(value, dict):
                _stringify_paths(value)
            elif isinstance(value, list):
                _stringify_paths(value)
    elif isinstance(obj, list):
        for item in obj:
            _stringify_paths(item)
