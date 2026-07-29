"""Configuration models for Local CI.

Handles loading, validating, and merging configuration from .localci.yml files.
Uses Pydantic v2 for type-safe validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from localci.errors import ConfigFileNotFoundError, ConfigIOError, ConfigValidationError

logger = logging.getLogger(__name__)


def _expand_path(v: Path) -> Path:
    """Expand ~ and resolve to an absolute path."""
    return Path(v).expanduser().resolve()


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ResourceLimitConfig(BaseModel):
    """Resource limits for parallel execution."""

    model_config = ConfigDict(extra="forbid")

    cpu_percent: int = Field(default=80, ge=1, le=100)
    memory_percent: int = Field(default=70, ge=1, le=100)


class ParallelConfig(BaseModel):
    """Parallelism settings."""

    model_config = ConfigDict(extra="forbid")

    max_jobs: int = Field(default=8, ge=1, le=64)
    resource_limit: ResourceLimitConfig = Field(
        default_factory=ResourceLimitConfig,
    )


class PlatformConfig(BaseModel):
    """Which platforms to run locally.

    Local CI executes Linux jobs in Docker. Windows and macOS workflow entries
    are not runnable; with the platform flag off (default) they fail loud,
    and when set to ``true`` they are skipped without failing the run.
    """

    linux: bool = True
    windows: bool = False
    macos: bool = False


class JobsConfig(BaseModel):
    """Job include/exclude filters."""

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MatrixFilter(BaseModel):
    """Single matrix filter entry."""

    compiler: str | None = None
    version: str | None = None
    name: str | None = None
    asan: bool | None = None
    ubsan: bool | None = None


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

    model_config = ConfigDict(extra="forbid")

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
    dir: Path | None = None  # default: cache.directory / "ccache"


class BoostCacheConfig(BaseModel):
    """Boost dependency cache settings."""

    enabled: bool = True
    branch: str = "develop"
    dir: Path | None = None  # default: cache.directory / "boost"
    shallow: bool = True
    remote: str | None = None  # default: https://github.com/boostorg/boost.git
    # When True, cache b2 build artifacts (bin.v2) per job so b2 does incremental builds
    build_dir: bool = True


class CmakeCacheConfig(BaseModel):
    """CMake configuration cache settings (per job/matrix)."""

    enabled: bool = True
    dir: Path | None = (
        None  # base dir; per-job path is dir / <job_matrix_key>[_<input_digest>]
    )
    # Optional: paths/globs relative to project root included in change detection (default: CMakeLists.txt, cmake/*.cmake)
    inputs: list[str] | None = None


class AptCacheConfig(BaseModel):
    """APT package install cache (main packages installation step).

    When the workflow runs an apt-get install step (e.g. package-install),
    bind-mounting a host directory over the container's /var/cache/apt/archives
    persists .deb files across runs so subsequent runs reuse them.
    """

    enabled: bool = True
    dir: Path | None = (
        None  # default: cache.directory / "apt"; per-job: dir / <queue_key>
    )


class CacheConfig(BaseModel):
    """Build caching settings."""

    enabled: bool = True
    directory: Path = Field(default_factory=lambda: Path.home() / ".localci" / "cache")
    ccache: CcacheConfig = Field(default_factory=CcacheConfig)
    boost: BoostCacheConfig = Field(default_factory=BoostCacheConfig)
    cmake: CmakeCacheConfig = Field(default_factory=CmakeCacheConfig)
    apt: AptCacheConfig = Field(default_factory=AptCacheConfig)

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

    model_config = ConfigDict(extra="forbid")

    timeout: int = Field(default=3600, ge=1)
    keep_containers: bool = False
    stop_on_first_failure: bool = False


# Known built-in workflow patch steps (order matches default pipeline sequence).
PATCH_STEP_NAMES: tuple[str, ...] = (
    "container_mounts",
    "b2_source_cache",
    "restore_capy_timestamps",
    "capy_copy_preservation",
    "b2_bootstrap_skip",
    "image_substitution",
    "codecov_skip",
)


PatchProfile = Literal["generic", "capy"]

CAPY_REPO_FULL_NAME = "cppalliance/capy"
CAPY_NATIVE_IMAGE_PREFIX = "capy-"
GENERIC_REPO_FULL_NAME = ""
GENERIC_NATIVE_IMAGE_PREFIX = ""

_CAPY_PATCH_STEPS: tuple[str, ...] = (
    "b2_source_cache",
    "restore_capy_timestamps",
    "capy_copy_preservation",
    "b2_bootstrap_skip",
)


def _is_capy_profile(patches: Any) -> bool:
    """Return whether raw or parsed patch config selects the Capy profile."""
    if isinstance(patches, dict):
        return patches.get("profile") == "capy"
    if patches is not None:
        return getattr(patches, "profile", None) == "capy"
    return False


def _apply_capy_project_defaults(project_data: dict[str, Any]) -> None:
    """Fill Capy project identity when fields are omitted or still generic."""
    if (
        "repo_full_name" not in project_data
        or project_data["repo_full_name"] == GENERIC_REPO_FULL_NAME
    ):
        project_data["repo_full_name"] = CAPY_REPO_FULL_NAME
    if (
        "native_image_prefix" not in project_data
        or project_data["native_image_prefix"] == GENERIC_NATIVE_IMAGE_PREFIX
    ):
        project_data["native_image_prefix"] = CAPY_NATIVE_IMAGE_PREFIX


class PatchProjectConfig(BaseModel):
    """Project-specific literals for C++/Boost-oriented patch steps.

    Defaults preserve Boost.Capy workflow behaviour when ``patches.profile`` is
    ``capy``. Override for other projects.
    """

    boost_source_copy_command: str = "cp -rL boost-source boost-root"
    boost_root_dir: str = "boost-root"
    patch_dependency_step_name: str = "Patch Boost"
    project_source_dir: str = "capy-root"
    restore_timestamps_step_title: str = "Restore capy source file timestamps"
    file_stats_basename: str = ".capy-file-stats"
    workspace_libs_copy_marker: str = 'cp -r "$workspace_root"'
    workspace_libs_copy_dest: str = "libs/$module"
    b2_workflow_action_marker: str = "b2-workflow"
    cached_module_libs_path: str = "libs/capy"


class PatchesConfig(BaseModel):
    """Workflow patch pipeline settings (enable/disable individual patch types)."""

    profile: PatchProfile = "generic"
    container_mounts: bool = True
    b2_source_cache: bool = False
    restore_capy_timestamps: bool = False
    capy_copy_preservation: bool = False
    b2_bootstrap_skip: bool = False
    image_substitution: bool = True
    codecov_skip: bool = True
    order: list[str] | None = None
    project: PatchProjectConfig = Field(default_factory=PatchProjectConfig)
    extra_steps: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def apply_profile_defaults(cls, data: Any) -> Any:
        """Enable Capy patch steps when ``profile: capy`` unless explicitly set."""
        if not isinstance(data, dict):
            return data
        if not _is_capy_profile(data):
            return data
        for step in _CAPY_PATCH_STEPS:
            data.setdefault(step, True)
        return data

    @field_validator("extra_steps")
    @classmethod
    def validate_extra_steps(cls, v: dict[str, bool]) -> dict[str, bool]:
        overlap = set(v.keys()) & set(PATCH_STEP_NAMES)
        if overlap:
            raise ValueError(
                f"extra_steps must not duplicate built-in patch steps: "
                f"{sorted(overlap)}"
            )
        from localci.core.patch_registry import get_patch_step_registry

        builtin = set(PATCH_STEP_NAMES)
        registered = set(get_patch_step_registry().keys()) - builtin
        unknown = set(v.keys()) - registered
        if unknown:
            raise ValueError(
                f"Unknown extra patch steps (register via localci.patch_steps "
                f"entry points): {sorted(unknown)}"
            )
        return v

    @field_validator("order")
    @classmethod
    def validate_order(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError(
                "order must not be empty; omit the field to use the default order"
            )
        if len(v) != len(set(v)):
            raise ValueError("Duplicate step names in 'order'")
        from localci.core.patch_registry import get_patch_step_registry

        unknown = set(v) - set(get_patch_step_registry().keys())
        if unknown:
            raise ValueError(f"Unknown patch steps: {sorted(unknown)}")
        return v

    @model_validator(mode="after")
    def validate_order_completeness(self) -> PatchesConfig:
        if self.order is None:
            return self
        enabled = {n for n in PATCH_STEP_NAMES if self.is_step_enabled(n)}
        enabled |= {n for n, on in self.extra_steps.items() if on}
        missing = enabled - set(self.order)
        if missing:
            raise ValueError(
                f"Patch steps are enabled but missing from 'order': {sorted(missing)}"
            )
        return self

    def is_step_enabled(self, name: str) -> bool:
        """Return whether patch step *name* is enabled in this config."""
        if name in PATCH_STEP_NAMES:
            return bool(getattr(self, name, True))
        return bool(self.extra_steps.get(name, False))

    def resolved_order(self) -> list[str]:
        """Return pipeline order: explicit ``order`` or built-ins + enabled plugins."""
        if self.order is not None:
            return self.order
        order = list(PATCH_STEP_NAMES)
        order.extend(
            name for name in sorted(self.extra_steps) if self.extra_steps[name]
        )
        return order


class ProjectConfig(BaseModel):
    """Project identity and image conventions for act command building."""

    model_config = ConfigDict(extra="forbid")

    repo_full_name: str = GENERIC_REPO_FULL_NAME
    native_image_prefix: str = GENERIC_NATIVE_IMAGE_PREFIX


# ---------------------------------------------------------------------------
# Root configuration model
# ---------------------------------------------------------------------------


class LocalCIConfig(BaseModel):
    """Root configuration model for .localci.yml.

    Unknown keys are rejected at the root and under orchestrator-related
    sub-models (``parallel``, ``execution``, ``images``, ``project``).
    Other sections (``cache``, ``logging``, ``patches``, etc.) still use
    Pydantic's default ``extra="ignore"`` until strict validation widens.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def apply_capy_project_defaults(cls, data: Any) -> Any:
        """Restore Capy project identity when ``patches.profile`` is ``capy``."""
        if not isinstance(data, dict):
            return data
        if not _is_capy_profile(data.get("patches")):
            return data
        project = data.get("project")
        if project is None:
            data["project"] = {
                "repo_full_name": CAPY_REPO_FULL_NAME,
                "native_image_prefix": CAPY_NATIVE_IMAGE_PREFIX,
            }
            return data
        if isinstance(project, dict):
            project_data = project
        elif isinstance(project, BaseModel):
            project_data = project.model_dump()
        else:
            return data
        _apply_capy_project_defaults(project_data)
        data["project"] = project_data
        return data

    version: int = 1
    workflow: Path = Field(
        default=Path(".github/workflows/ci.yml"),
        validate_default=True,
    )
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
    patches: PatchesConfig = Field(default_factory=PatchesConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)


# ---------------------------------------------------------------------------
# Cache path resolution (Phase 2)
# ---------------------------------------------------------------------------

# Container paths used when bind-mounting host cache dirs (act --container-options -v ...)
LOCALCI_CACHE_CONTAINER_ROOT = "/tmp/localci-cache"


# Container path for APT archives (standard Debian/Ubuntu location)
APT_ARCHIVES_CONTAINER = "/var/cache/apt/archives"


@dataclass
class ResolvedCachePaths:
    """Resolved host paths and container paths for Phase 2 caches."""

    ccache_host: Path | None = None
    boost_host: Path | None = None
    cmake_host: Path | None = None  # per-job: cache_base / job_id / matrix_key
    b2_source_host: Path | None = (
        None  # per-job: persistent boost-root (source + bin.v2 artifacts)
    )
    apt_host: Path | None = None  # per-job: apt .deb cache for "Install packages" step

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

    @property
    def apt_container(self) -> str:
        return APT_ARCHIVES_CONTAINER

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
        if self.apt_host is not None:
            out.append(self.apt_host)
        return out


def resolve_cache_paths(
    cache_config: CacheConfig,
    no_cache: bool,
    cache_dir_override: Path | None = None,
    job_id: str | None = None,
    queue_key: str | None = None,
    cmake_input_digest: str | None = None,
) -> ResolvedCachePaths | None:
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
        subdir = f"{safe_key}_{cmake_input_digest}" if cmake_input_digest else safe_key
        r.cmake_host = base / subdir
    if cache_config.apt.enabled and job_id and queue_key:
        base = cache_config.apt.dir or root / "apt"
        base = Path(base).expanduser().resolve()
        safe_key = queue_key.replace(":", "-")
        r.apt_host = base / safe_key

    if (
        r.ccache_host is None
        and r.boost_host is None
        and r.cmake_host is None
        and r.b2_source_host is None
        and r.apt_host is None
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
    config_path: Path | None
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigFileNotFoundError(config_path)
    else:
        config_path = find_config_file()

    if config_path is None:
        logger.debug("No config file found, using defaults")
        return LocalCIConfig()

    logger.debug("Loading config from %s", config_path)
    try:
        with open(config_path, encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigIOError(config_path, exc) from exc

    try:
        return LocalCIConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigValidationError(config_path, exc) from exc


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
            elif isinstance(value, (dict, list)):
                _stringify_paths(value)
    elif isinstance(obj, list):
        for item in obj:
            _stringify_paths(item)
