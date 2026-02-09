"""Unit tests for the Workflow Analyzer module (Issue 2).

Uses the ``sample_ci.yml`` fixture which mirrors capy's CI structure
with 5 matrix entries across Linux, Windows, and macOS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localci.core.workflow import (
    BuildSystem,
    BuildVariant,
    CompilerFamily,
    CompilerInfo,
    ContainerInfo,
    Job,
    MatrixEntry,
    MissingFieldError,
    PackageRequirements,
    Platform,
    StepInfo,
    Workflow,
    WorkflowAnalyzer,
    WorkflowError,
    WorkflowParseError,
)
from localci.core.serialization import (
    workflow_summary,
    workflow_to_dict,
    workflow_to_json,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def analyzer():
    return WorkflowAnalyzer()


@pytest.fixture
def sample_workflow(analyzer):
    return analyzer.analyze(FIXTURES_DIR / "sample_ci.yml")


# =====================================================================
# WorkflowAnalyzer -- main functionality
# =====================================================================


class TestWorkflowAnalyzer:
    """Top-level workflow parsing."""

    def test_parse_name(self, sample_workflow):
        assert sample_workflow.name == "CI Test"

    def test_parse_events(self, sample_workflow):
        assert "push" in sample_workflow.events
        assert "pull_request" in sample_workflow.events

    def test_parse_env(self, sample_workflow):
        assert sample_workflow.env.get("PYTHON_VERSION") == "3.11"

    def test_job_count(self, sample_workflow):
        assert sample_workflow.total_jobs == 2
        assert "build" in sample_workflow.jobs
        assert "changelog" in sample_workflow.jobs

    def test_matrix_count(self, sample_workflow):
        assert len(sample_workflow.jobs["build"].matrix) == 5

    def test_total_matrix_entries(self, sample_workflow):
        # 5 from build + 1 from changelog (no matrix)
        assert sample_workflow.total_matrix_entries == 6

    def test_file_path(self, sample_workflow):
        assert sample_workflow.file_path.name == "sample_ci.yml"


# =====================================================================
# Matrix entry parsing
# =====================================================================


class TestMatrixEntryParsing:
    """Individual matrix entry parsing."""

    def test_gcc_entry(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert gcc.name == "GCC 15: C++20"
        assert gcc.compiler.family == CompilerFamily.GCC
        assert gcc.compiler.version == "15"
        assert gcc.compiler.cc == "gcc-15"
        assert gcc.compiler.cxx == "g++-15"
        assert gcc.compiler.b2_toolset == "gcc"
        assert gcc.platform == Platform.LINUX
        assert gcc.container.image == "ubuntu:25.04"
        assert gcc.architecture == "x86_64"
        assert gcc.variant.shared is True
        assert gcc.variant.build_type == "Release"

    def test_clang_entry(self, sample_workflow):
        clang = sample_workflow.jobs["build"].matrix[1]
        assert clang.name == "Clang 20: C++20-23"
        assert clang.compiler.family == CompilerFamily.CLANG
        assert clang.compiler.cxxstd == ["20", "23"]
        assert clang.platform == Platform.LINUX
        assert clang.container.image == "ubuntu:24.04"

    def test_msvc_entry(self, sample_workflow):
        msvc = sample_workflow.jobs["build"].matrix[2]
        assert msvc.name == "MSVC 14.42: C++20"
        assert msvc.compiler.family == CompilerFamily.MSVC
        assert msvc.compiler.version == "14.42"
        assert msvc.platform == Platform.WINDOWS
        assert msvc.container.image is None
        assert msvc.generator == "Visual Studio 17 2022"

    def test_apple_clang_entry(self, sample_workflow):
        ac = sample_workflow.jobs["build"].matrix[3]
        assert ac.name == "Apple-Clang: asan+ubsan"
        assert ac.compiler.family == CompilerFamily.APPLE_CLANG
        assert ac.platform == Platform.MACOS
        assert ac.variant.asan is True
        assert ac.variant.ubsan is True

    def test_coverage_entry(self, sample_workflow):
        cov = sample_workflow.jobs["build"].matrix[4]
        assert cov.name == "GCC 13: C++20 coverage"
        assert cov.variant.coverage is True
        assert cov.compiler.family == CompilerFamily.GCC
        assert cov.compiler.version == "13"


# =====================================================================
# Platform classification
# =====================================================================


class TestPlatformClassification:
    """Platform detection from runs-on and container."""

    def test_platform_summary(self, sample_workflow):
        summary = sample_workflow.platform_summary()
        assert summary[Platform.LINUX] >= 2
        assert summary[Platform.WINDOWS] == 1
        assert summary[Platform.MACOS] == 1

    def test_filter_linux(self, analyzer, sample_workflow):
        linux = analyzer.filter_by_platform(sample_workflow, Platform.LINUX)
        assert len(linux) >= 2
        assert all(e.platform == Platform.LINUX for e in linux)

    def test_filter_windows(self, analyzer, sample_workflow):
        win = analyzer.filter_by_platform(sample_workflow, Platform.WINDOWS)
        assert len(win) == 1
        assert win[0].compiler.family == CompilerFamily.MSVC

    def test_filter_macos(self, analyzer, sample_workflow):
        mac = analyzer.filter_by_platform(sample_workflow, Platform.MACOS)
        assert len(mac) == 1
        assert mac[0].compiler.family == CompilerFamily.APPLE_CLANG


# =====================================================================
# Compiler classification
# =====================================================================


class TestCompilerClassification:
    """Compiler detection."""

    def test_filter_gcc(self, analyzer, sample_workflow):
        gcc = analyzer.filter_by_compiler(sample_workflow, CompilerFamily.GCC)
        assert len(gcc) == 2  # GCC 15 + GCC 13

    def test_filter_clang(self, analyzer, sample_workflow):
        clang = analyzer.filter_by_compiler(sample_workflow, CompilerFamily.CLANG)
        assert len(clang) == 1

    def test_filter_msvc(self, analyzer, sample_workflow):
        msvc = analyzer.filter_by_compiler(sample_workflow, CompilerFamily.MSVC)
        assert len(msvc) == 1

    def test_filter_apple_clang(self, analyzer, sample_workflow):
        ac = analyzer.filter_by_compiler(
            sample_workflow, CompilerFamily.APPLE_CLANG
        )
        assert len(ac) == 1

    def test_compiler_display_name(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert gcc.compiler.display_name == "GCC 15"
        msvc = sample_workflow.jobs["build"].matrix[2]
        assert msvc.compiler.display_name == "MSVC 14.42"


# =====================================================================
# Build system detection
# =====================================================================


class TestBuildSystemDetection:
    """Build system detection from matrix flags and steps."""

    def test_b2_and_cmake(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert gcc.build_system == BuildSystem.BOTH

    def test_coverage_is_cmake(self, sample_workflow):
        cov = sample_workflow.jobs["build"].matrix[4]
        # coverage entries get cmake; the B2 step is skipped because of
        # the ``!matrix.coverage`` condition
        assert cov.build_system == BuildSystem.CMAKE


# =====================================================================
# Variant filtering
# =====================================================================


class TestVariantFiltering:
    """Variant-based filtering."""

    def test_filter_asan(self, analyzer, sample_workflow):
        asan = analyzer.filter_by_variant(sample_workflow, asan=True)
        assert len(asan) == 1
        assert asan[0].variant.ubsan is True  # apple-clang has both

    def test_filter_coverage(self, analyzer, sample_workflow):
        cov = analyzer.filter_by_variant(sample_workflow, coverage=True)
        assert len(cov) == 1
        assert "GCC 13" in cov[0].name

    def test_variant_label(self, sample_workflow):
        ac = sample_workflow.jobs["build"].matrix[3]
        assert "asan" in ac.variant.label
        assert "ubsan" in ac.variant.label

    def test_standard_label(self):
        v = BuildVariant()
        assert v.label == "standard"


# =====================================================================
# Search
# =====================================================================


class TestSearch:
    """Search by name pattern."""

    def test_search_by_name(self, analyzer, sample_workflow):
        results = analyzer.search(sample_workflow, "gcc")
        assert len(results) == 2  # GCC 15 + GCC 13

    def test_search_case_insensitive(self, analyzer, sample_workflow):
        results = analyzer.search(sample_workflow, "CLANG")
        assert len(results) >= 1

    def test_search_no_match(self, analyzer, sample_workflow):
        results = analyzer.search(sample_workflow, "nonexistent")
        assert len(results) == 0


# =====================================================================
# Job dependencies
# =====================================================================


class TestDependencyOrder:
    """Topological sort of jobs."""

    def test_dependency_order(self, sample_workflow):
        order = sample_workflow.dependency_order()
        assert "build" in order
        assert "changelog" in order
        # changelog depends on build, so build comes first
        assert order.index("build") < order.index("changelog")

    def test_changelog_needs_build(self, sample_workflow):
        changelog = sample_workflow.jobs["changelog"]
        assert "build" in changelog.needs


# =====================================================================
# Container info
# =====================================================================


class TestContainerInfo:
    """Container parsing."""

    def test_container_os(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert gcc.container.os_name == "ubuntu"
        assert gcc.container.os_version == "25.04"

    def test_no_container(self, sample_workflow):
        msvc = sample_workflow.jobs["build"].matrix[2]
        assert msvc.container.image is None
        assert msvc.is_containerized is False

    def test_is_containerized(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert gcc.is_containerized is True


# =====================================================================
# Package requirements
# =====================================================================


class TestPackageRequirements:
    """Package requirements extraction."""

    def test_gcc_packages(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert "gcc-15" in gcc.packages.apt_packages
        assert "g++-15" in gcc.packages.apt_packages
        assert "libssl-dev" in gcc.packages.apt_packages

    def test_cmake_build_tool(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[0]
        assert "cmake" in gcc.packages.build_tools

    def test_coverage_packages(self, sample_workflow):
        cov = sample_workflow.jobs["build"].matrix[4]
        assert "lcov" in cov.packages.apt_packages

    def test_all_packages(self):
        pkg = PackageRequirements(
            apt_packages=["foo"], build_tools=["bar"]
        )
        assert pkg.all_packages == ["foo", "bar"]


# =====================================================================
# Steps
# =====================================================================


class TestSteps:
    """Step parsing."""

    def test_step_count(self, sample_workflow):
        build = sample_workflow.jobs["build"]
        assert len(build.steps) == 3  # checkout, b2, cmake

    def test_checkout_step(self, sample_workflow):
        checkout = sample_workflow.jobs["build"].steps[0]
        assert checkout.name == "Checkout"
        assert checkout.is_action is True
        assert checkout.action_name == "checkout"

    def test_b2_step(self, sample_workflow):
        b2 = sample_workflow.jobs["build"].steps[1]
        assert b2.name == "B2 Build"
        assert b2.action_name == "b2-workflow"

    def test_cmake_step(self, sample_workflow):
        cmake = sample_workflow.jobs["build"].steps[2]
        assert cmake.name == "CMake Build"
        assert cmake.action_name == "cmake-workflow"


# =====================================================================
# Serialization
# =====================================================================


class TestSerialization:
    """JSON / dict serialization."""

    def test_to_json(self, sample_workflow):
        j = workflow_to_json(sample_workflow)
        data = json.loads(j)
        assert data["name"] == "CI Test"
        assert "jobs" in data

    def test_to_dict(self, sample_workflow):
        d = workflow_to_dict(sample_workflow)
        assert isinstance(d, dict)
        assert d["name"] == "CI Test"

    def test_summary(self, sample_workflow):
        s = workflow_summary(sample_workflow)
        assert s["name"] == "CI Test"
        assert s["total_jobs"] == 2
        assert "linux" in s["platform_summary"]
        assert "dependency_order" in s


# =====================================================================
# Data-model unit tests
# =====================================================================


class TestDataModels:
    """Direct tests for data-classes and enums."""

    def test_compiler_info_display_name(self):
        ci = CompilerInfo(family=CompilerFamily.GCC, version="15")
        assert ci.display_name == "GCC 15"

    def test_compiler_info_wildcard(self):
        ci = CompilerInfo(family=CompilerFamily.MINGW, version="*")
        assert ci.is_wildcard is True

    def test_apple_clang_display(self):
        ci = CompilerInfo(family=CompilerFamily.APPLE_CLANG, version="16")
        assert ci.display_name == "Apple-Clang 16"

    def test_matrix_entry_image_key(self):
        entry = MatrixEntry(
            index=0,
            name="test",
            platform=Platform.LINUX,
            compiler=CompilerInfo(family=CompilerFamily.GCC, version="15"),
            container=ContainerInfo(image="ubuntu:25.04"),
            variant=BuildVariant(),
            packages=PackageRequirements(),
            runs_on="ubuntu-latest",
            build_system=BuildSystem.B2,
        )
        assert entry.image_requirements_key == "ubuntu:25.04-gcc-15-x86_64"

    def test_job_entries_by_platform(self):
        entry_linux = MatrixEntry(
            index=0,
            name="L",
            platform=Platform.LINUX,
            compiler=CompilerInfo(family=CompilerFamily.GCC, version="15"),
            container=ContainerInfo(),
            variant=BuildVariant(),
            packages=PackageRequirements(),
            runs_on="ubuntu-latest",
            build_system=BuildSystem.B2,
        )
        entry_win = MatrixEntry(
            index=1,
            name="W",
            platform=Platform.WINDOWS,
            compiler=CompilerInfo(family=CompilerFamily.MSVC, version="14"),
            container=ContainerInfo(),
            variant=BuildVariant(),
            packages=PackageRequirements(),
            runs_on="windows-2022",
            build_system=BuildSystem.B2,
        )
        job = Job(
            id="build",
            name="Build",
            runs_on="ubuntu-latest",
            matrix=[entry_linux, entry_win],
        )
        assert len(job.entries_by_platform(Platform.LINUX)) == 1
        assert len(job.entries_by_compiler(CompilerFamily.MSVC)) == 1

    def test_step_info_non_action(self):
        step = StepInfo(name="run stuff", run="echo hello")
        assert step.is_action is False
        assert step.action_name is None


# =====================================================================
# Edge cases
# =====================================================================


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_nonexistent_file(self, analyzer):
        with pytest.raises(FileNotFoundError):
            analyzer.analyze(Path("/nonexistent/ci.yml"))

    def test_empty_matrix(self, sample_workflow):
        changelog = sample_workflow.jobs["changelog"]
        assert changelog.has_matrix is False
        assert changelog.total_configurations == 1

    def test_error_classes(self):
        err = WorkflowParseError(Path("ci.yml"), "bad yaml")
        assert "ci.yml" in str(err)

        err2 = MissingFieldError("compiler", "matrix entry 3")
        assert "compiler" in str(err2)

    def test_analyze_sample_workflow_fixture(self, analyzer):
        """Also parse the original sample_workflow.yml (Issue 1 fixture)."""
        fixture = FIXTURES_DIR / "sample_workflow.yml"
        if fixture.exists():
            wf = analyzer.analyze(fixture)
            assert wf.total_jobs >= 1
