"""Workflow analysis: data models and analyzer.

Parses GitHub Actions YAML workflow files to extract jobs, matrix
configurations, dependencies, and environment requirements.  The structured
data produced here is consumed by the executor, image matcher, and
orchestrator modules downstream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =====================================================================
# Enums
# =====================================================================


class Platform(Enum):
    """Target platform classification."""

    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    UNKNOWN = "unknown"


class CompilerFamily(Enum):
    """Compiler family classification."""

    GCC = "gcc"
    CLANG = "clang"
    APPLE_CLANG = "apple-clang"
    MSVC = "msvc"
    MINGW = "mingw"
    UNKNOWN = "unknown"


class BuildSystem(Enum):
    """Build system used by a matrix entry."""

    B2 = "b2"
    CMAKE = "cmake"
    BOTH = "both"


# =====================================================================
# Data-classes
# =====================================================================


@dataclass
class CompilerInfo:
    """Compiler specification."""

    family: CompilerFamily
    version: str  # "15", "20", "14.42", "*"
    cc: Optional[str] = None
    cxx: Optional[str] = None
    cxxstd: list[str] = field(default_factory=lambda: ["20"])
    b2_toolset: Optional[str] = None

    @property
    def display_name(self) -> str:
        """e.g. 'GCC 15', 'Clang 20', 'MSVC 14.42'."""
        family_label = self.family.value
        if self.family == CompilerFamily.APPLE_CLANG:
            family_label = "Apple-Clang"
        else:
            family_label = self.family.value.upper()
        return f"{family_label} {self.version}"

    @property
    def is_wildcard(self) -> bool:
        return self.version == "*"


@dataclass
class ContainerInfo:
    """Container specification."""

    image: Optional[str] = None
    options: Optional[str] = None

    @property
    def os_name(self) -> Optional[str]:
        """Extract OS name from image, e.g. 'ubuntu'."""
        if self.image:
            return self.image.split(":")[0]
        return None

    @property
    def os_version(self) -> Optional[str]:
        """Extract OS version from image, e.g. '25.04'."""
        if self.image and ":" in self.image:
            return self.image.split(":")[1]
        return None


@dataclass
class BuildVariant:
    """Build variant flags."""

    shared: bool = False
    asan: bool = False
    ubsan: bool = False
    coverage: bool = False
    x86: bool = False
    build_type: str = "Release"
    time_trace: bool = False
    valgrind: bool = False

    @property
    def sanitizers(self) -> list[str]:
        s: list[str] = []
        if self.asan:
            s.append("asan")
        if self.ubsan:
            s.append("ubsan")
        return s

    @property
    def label(self) -> str:
        """Human-readable variant label, e.g. 'asan+ubsan', 'coverage'."""
        parts: list[str] = []
        if self.shared:
            parts.append("shared")
        if self.sanitizers:
            parts.append("+".join(self.sanitizers))
        if self.coverage:
            parts.append("coverage")
        if self.x86:
            parts.append("x86")
        return ", ".join(parts) or "standard"


@dataclass
class PackageRequirements:
    """Required system packages and tools."""

    apt_packages: list[str] = field(default_factory=list)
    apt_add_architecture: Optional[str] = None  # e.g. "i386"
    build_tools: list[str] = field(default_factory=list)
    cxxflags: Optional[str] = None
    ccflags: Optional[str] = None

    @property
    def all_packages(self) -> list[str]:
        return self.apt_packages + self.build_tools


@dataclass
class MatrixEntry:
    """Single matrix configuration (fully resolved)."""

    index: int
    name: str
    platform: Platform
    compiler: CompilerInfo
    container: ContainerInfo
    variant: BuildVariant
    packages: PackageRequirements
    runs_on: str
    build_system: BuildSystem
    architecture: str = "x86_64"
    generator: Optional[str] = None
    timeout_minutes: int = 120
    raw: dict = field(default_factory=dict)

    @property
    def image_requirements_key(self) -> str:
        """Unique key for image matching."""
        os = self.container.image or self.runs_on
        return f"{os}-{self.compiler.family.value}-{self.compiler.version}-{self.architecture}"

    @property
    def is_containerized(self) -> bool:
        return self.container.image is not None


@dataclass
class StepInfo:
    """Workflow step information."""

    name: str
    uses: Optional[str] = None
    run: Optional[str] = None
    condition: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    with_: dict[str, Any] = field(default_factory=dict)

    @property
    def is_action(self) -> bool:
        return self.uses is not None

    @property
    def action_name(self) -> Optional[str]:
        """Extract action name, e.g. 'b2-workflow' from the uses path."""
        if self.uses:
            parts = self.uses.split("/")
            if len(parts) >= 2:
                return parts[-1].split("@")[0]
        return None


@dataclass
class Job:
    """Workflow job definition."""

    id: str
    name: str
    runs_on: str
    container: Optional[ContainerInfo] = None
    needs: list[str] = field(default_factory=list)
    condition: Optional[str] = None
    strategy: Optional[dict] = None
    matrix: list[MatrixEntry] = field(default_factory=list)
    steps: list[StepInfo] = field(default_factory=list)
    timeout_minutes: int = 60
    env: dict[str, str] = field(default_factory=dict)
    defaults: Optional[dict] = None

    @property
    def has_matrix(self) -> bool:
        return len(self.matrix) > 0

    @property
    def total_configurations(self) -> int:
        return max(len(self.matrix), 1)

    def entries_by_platform(self, platform: Platform) -> list[MatrixEntry]:
        return [e for e in self.matrix if e.platform == platform]

    def entries_by_compiler(self, family: CompilerFamily) -> list[MatrixEntry]:
        return [e for e in self.matrix if e.compiler.family == family]


@dataclass
class Workflow:
    """Complete workflow definition."""

    name: str
    file_path: Path
    events: list[str]
    env: dict[str, str] = field(default_factory=dict)
    concurrency: Optional[dict] = None
    jobs: dict[str, Job] = field(default_factory=dict)

    @property
    def total_jobs(self) -> int:
        return len(self.jobs)

    @property
    def total_matrix_entries(self) -> int:
        return sum(j.total_configurations for j in self.jobs.values())

    def all_matrix_entries(self) -> list[MatrixEntry]:
        entries: list[MatrixEntry] = []
        for job in self.jobs.values():
            entries.extend(job.matrix)
        return entries

    def platform_summary(self) -> dict[Platform, int]:
        summary: dict[Platform, int] = {p: 0 for p in Platform}
        for entry in self.all_matrix_entries():
            summary[entry.platform] += 1
        return {k: v for k, v in summary.items() if v > 0}

    def dependency_order(self) -> list[str]:
        """Topological sort of jobs by dependencies."""
        visited: set[str] = set()
        order: list[str] = []

        def visit(job_id: str) -> None:
            if job_id in visited:
                return
            visited.add(job_id)
            if job_id in self.jobs:
                for dep in self.jobs[job_id].needs:
                    visit(dep)
            order.append(job_id)

        for job_id in self.jobs:
            visit(job_id)

        return order


# =====================================================================
# Errors
# =====================================================================


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


# =====================================================================
# WorkflowAnalyzer
# =====================================================================


class WorkflowAnalyzer:
    """Analyze GitHub Actions workflow files.

    Parses workflow YAML via :class:`~localci.utils.yq.YqWrapper` and
    produces structured :class:`Workflow` objects with fully resolved
    matrix configurations.

    Usage::

        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(Path(".github/workflows/ci.yml"))

        # Filter by platform
        linux_entries = analyzer.filter_by_platform(workflow, Platform.LINUX)

        # Dependency order
        order = workflow.dependency_order()
    """

    def __init__(self) -> None:
        from localci.utils.yq import YqWrapper

        self.yq = YqWrapper()

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------

    def analyze(self, workflow_path: Path, event: Optional[str] = None) -> Workflow:
        """Parse a workflow file and return a structured :class:`Workflow`.

        Parameters
        ----------
        workflow_path:
            Path to a ``.github/workflows/*.yml`` file.
        event:
            Optional event filter (``push``, ``pull_request``).

        Returns
        -------
        Workflow
            Fully parsed workflow with jobs and matrix entries.
        """
        logger.info("Analyzing workflow: %s", workflow_path)

        name = self.yq.workflow_name(workflow_path)
        events = self.yq.events(workflow_path)
        env = self.yq.global_env(workflow_path)
        concurrency = self.yq.concurrency(workflow_path)

        job_ids = self.yq.job_names(workflow_path)
        jobs: dict[str, Job] = {}

        for job_id in job_ids:
            logger.debug("Parsing job: %s", job_id)
            jobs[job_id] = self._parse_job(workflow_path, job_id)

        workflow = Workflow(
            name=name,
            file_path=workflow_path,
            events=events,
            env=env,
            concurrency=concurrency,
            jobs=jobs,
        )

        logger.info(
            "Analysis complete: %d jobs, %d matrix entries",
            workflow.total_jobs,
            workflow.total_matrix_entries,
        )

        return workflow

    def analyze_multiple(self, workflow_dir: Path) -> list[Workflow]:
        """Analyze all workflow files in a directory."""
        workflows: list[Workflow] = []
        for yml in sorted(workflow_dir.glob("*.yml")):
            try:
                workflows.append(self.analyze(yml))
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", yml, exc)
        return workflows

    # -----------------------------------------------------------------
    # Job parsing
    # -----------------------------------------------------------------

    def _parse_job(self, file: Path, job_id: str) -> Job:
        """Parse a single job definition."""
        job_data = self.yq.job_data(file, job_id)

        name = job_data.get("name", job_id)
        runs_on = job_data.get("runs-on", "ubuntu-latest")
        needs = self._normalize_list(job_data.get("needs"))
        condition = job_data.get("if")
        timeout = job_data.get("timeout-minutes", 60)
        env = self._stringify_dict(job_data.get("env", {}))
        defaults = job_data.get("defaults")

        container = self._parse_container(job_data.get("container"))

        strategy = job_data.get("strategy")
        matrix: list[MatrixEntry] = []
        if strategy and "matrix" in strategy:
            include = strategy["matrix"].get("include", [])
            for i, entry in enumerate(include):
                matrix.append(self._parse_matrix_entry(i, entry, job_data))

        steps = [self._parse_step(s) for s in job_data.get("steps", [])]

        return Job(
            id=job_id,
            name=name,
            runs_on=runs_on,
            container=container,
            needs=needs,
            condition=condition,
            strategy=strategy,
            matrix=matrix,
            steps=steps,
            timeout_minutes=timeout,
            env=env,
            defaults=defaults,
        )

    # -----------------------------------------------------------------
    # Matrix entry parsing
    # -----------------------------------------------------------------

    def _parse_matrix_entry(
        self, index: int, entry: dict, job_data: dict
    ) -> MatrixEntry:
        """Parse a single matrix include entry."""
        compiler = self._parse_compiler(entry)
        container = self._parse_container(entry.get("container"))

        runs_on = entry.get("runs-on", job_data.get("runs-on", "ubuntu-latest"))
        platform = self._classify_platform(runs_on, container)

        architecture = "x86" if entry.get("x86") else "x86_64"

        variant = BuildVariant(
            shared=bool(entry.get("shared", False)),
            asan=bool(entry.get("asan", False)),
            ubsan=bool(entry.get("ubsan", False)),
            coverage=bool(entry.get("coverage", False)),
            x86=bool(entry.get("x86", False)),
            build_type=str(entry.get("build-type", "Release")),
            time_trace=bool(entry.get("time-trace", False)),
            valgrind=bool(entry.get("valgrind", False)),
        )

        packages = self._parse_packages(entry)
        build_system = self._detect_build_system(entry, job_data)

        return MatrixEntry(
            index=index,
            name=entry.get("name", f"Job {index}"),
            platform=platform,
            compiler=compiler,
            container=container,
            variant=variant,
            packages=packages,
            runs_on=runs_on,
            build_system=build_system,
            architecture=architecture,
            generator=entry.get("generator"),
            timeout_minutes=job_data.get("timeout-minutes", 120),
            raw=entry,
        )

    # -----------------------------------------------------------------
    # Field parsers
    # -----------------------------------------------------------------

    def _parse_compiler(self, entry: dict) -> CompilerInfo:
        family_str = entry.get("compiler", "unknown")
        family = self._classify_compiler(family_str)
        return CompilerInfo(
            family=family,
            version=str(entry.get("version", "*")),
            cc=entry.get("cc"),
            cxx=entry.get("cxx"),
            cxxstd=self._parse_cxxstd(entry.get("cxxstd", "20")),
            b2_toolset=entry.get("b2-toolset"),
        )

    def _parse_container(self, container_data: Any) -> ContainerInfo:
        if container_data is None:
            return ContainerInfo()
        if isinstance(container_data, str):
            return ContainerInfo(image=container_data)
        if isinstance(container_data, dict):
            return ContainerInfo(
                image=container_data.get("image"),
                options=container_data.get("options"),
            )
        return ContainerInfo()

    def _parse_packages(self, entry: dict) -> PackageRequirements:
        install_str = entry.get("install", "")
        apt_packages = (
            [p.strip() for p in install_str.split() if p.strip()]
            if install_str
            else []
        )

        build_tools: list[str] = []
        if entry.get("build-cmake"):
            build_tools.append("cmake")
        if entry.get("coverage") and "lcov" not in apt_packages:
            build_tools.append("lcov")

        apt_add_arch = "i386" if entry.get("x86") else None

        return PackageRequirements(
            apt_packages=apt_packages,
            apt_add_architecture=apt_add_arch,
            build_tools=build_tools,
            cxxflags=entry.get("cxxflags"),
            ccflags=entry.get("ccflags"),
        )

    def _parse_step(self, step: dict) -> StepInfo:
        return StepInfo(
            name=step.get("name", ""),
            uses=step.get("uses"),
            run=step.get("run"),
            condition=step.get("if"),
            env=self._stringify_dict(step.get("env", {})),
            with_=step.get("with", {}),
        )

    # -----------------------------------------------------------------
    # Classification helpers
    # -----------------------------------------------------------------

    def _classify_platform(
        self, runs_on: str, container: ContainerInfo
    ) -> Platform:
        # Container-based jobs
        if container.image:
            img = container.image.lower()
            if any(
                kw in img
                for kw in ("ubuntu", "debian", "fedora", "centos", "alpine")
            ):
                return Platform.LINUX
            if "windows" in img:
                return Platform.WINDOWS

        # Runner-based jobs
        runs_lower = runs_on.lower()
        if "ubuntu" in runs_lower or "linux" in runs_lower:
            return Platform.LINUX
        if "windows" in runs_lower:
            return Platform.WINDOWS
        if "macos" in runs_lower:
            return Platform.MACOS

        return Platform.UNKNOWN

    _COMPILER_MAP: dict[str, CompilerFamily] = {
        "gcc": CompilerFamily.GCC,
        "clang": CompilerFamily.CLANG,
        "apple-clang": CompilerFamily.APPLE_CLANG,
        "msvc": CompilerFamily.MSVC,
        "mingw": CompilerFamily.MINGW,
    }

    def _classify_compiler(self, compiler_str: str) -> CompilerFamily:
        return self._COMPILER_MAP.get(compiler_str.lower(), CompilerFamily.UNKNOWN)

    def _detect_build_system(self, entry: dict, job_data: dict) -> BuildSystem:
        has_b2 = False
        has_cmake = False

        if entry.get("build-cmake"):
            has_cmake = True

        if entry.get("coverage"):
            has_cmake = True

        for step in job_data.get("steps", []):
            uses = step.get("uses", "")
            if "b2-workflow" in uses:
                condition = step.get("if", "")
                if "coverage" in condition and entry.get("coverage"):
                    continue
                if "time-trace" in condition and entry.get("time-trace"):
                    continue
                has_b2 = True
            if "cmake-workflow" in uses:
                condition = step.get("if", "")
                if any(
                    kw in condition
                    for kw in ("coverage", "build-cmake", "is-earliest")
                ):
                    if entry.get("coverage") or entry.get("build-cmake") or entry.get("is-earliest"):
                        has_cmake = True

        # Default: most entries run B2
        if not has_b2 and not has_cmake:
            has_b2 = True

        if has_b2 and has_cmake:
            return BuildSystem.BOTH
        if has_cmake:
            return BuildSystem.CMAKE
        return BuildSystem.B2

    # -----------------------------------------------------------------
    # Utility helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _parse_cxxstd(cxxstd: Any) -> list[str]:
        if isinstance(cxxstd, str):
            return [s.strip() for s in cxxstd.split(",")]
        if isinstance(cxxstd, (int, float)):
            return [str(int(cxxstd))]
        return ["20"]

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    @staticmethod
    def _stringify_dict(d: Any) -> dict[str, str]:
        """Ensure all values in a dict are strings."""
        if not isinstance(d, dict):
            return {}
        return {str(k): str(v) for k, v in d.items()}

    # -----------------------------------------------------------------
    # Filtering
    # -----------------------------------------------------------------

    def filter_by_platform(
        self, workflow: Workflow, platform: Platform
    ) -> list[MatrixEntry]:
        """Return matrix entries matching *platform*."""
        return [
            e for e in workflow.all_matrix_entries() if e.platform == platform
        ]

    def filter_by_compiler(
        self, workflow: Workflow, family: CompilerFamily
    ) -> list[MatrixEntry]:
        """Return matrix entries matching *family*."""
        return [
            e
            for e in workflow.all_matrix_entries()
            if e.compiler.family == family
        ]

    def filter_by_variant(
        self, workflow: Workflow, **kwargs: Any
    ) -> list[MatrixEntry]:
        """Filter by variant flags.

        Example::

            entries = analyzer.filter_by_variant(workflow, asan=True)
        """
        entries = workflow.all_matrix_entries()
        for key, value in kwargs.items():
            entries = [
                e for e in entries if getattr(e.variant, key, None) == value
            ]
        return entries

    def search(
        self, workflow: Workflow, query: str
    ) -> list[MatrixEntry]:
        """Search matrix entries by name pattern (case-insensitive)."""
        query_lower = query.lower()
        return [
            e
            for e in workflow.all_matrix_entries()
            if query_lower in e.name.lower()
        ]
