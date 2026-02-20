"""Unit tests for the Workflow Analyzer module (Issue 2).

Uses the ``sample_ci.yml`` fixture which mirrors capy's CI structure
with 14 matrix entries: 10 Linux, 3 Windows, 1 macOS.
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
    UnsupportedMatrixError,
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
        assert sample_workflow.env.get("NET_RETRY_COUNT") == "5"
        assert sample_workflow.env.get("GIT_FETCH_JOBS") == "8"

    def test_parse_concurrency(self, sample_workflow):
        assert sample_workflow.concurrency is not None
        assert sample_workflow.concurrency.get("cancel-in-progress") is True

    def test_job_count(self, sample_workflow):
        assert sample_workflow.total_jobs == 2
        assert "build" in sample_workflow.jobs
        assert "changelog" in sample_workflow.jobs

    def test_matrix_count(self, sample_workflow):
        assert len(sample_workflow.jobs["build"].matrix) == 14

    def test_total_matrix_entries(self, sample_workflow):
        # 14 from build + 1 from changelog (no matrix)
        assert sample_workflow.total_matrix_entries == 15

    def test_file_path(self, sample_workflow):
        assert sample_workflow.file_path.name == "sample_ci.yml"

    def test_event_warning(self, analyzer, caplog):
        """Passing an event not in the workflow logs a warning."""
        import logging

        with caplog.at_level(logging.WARNING):
            analyzer.analyze(
                FIXTURES_DIR / "sample_ci.yml", event="schedule"
            )
        assert "schedule" in caplog.text
    def test_analyze_with_event_filter_same_when_no_conditions(self, analyzer):
        """With no event-specific job conditions, --event does not change job set."""
        wf_no_event = analyzer.analyze(FIXTURES_DIR / "sample_ci.yml")
        wf_push = analyzer.analyze(FIXTURES_DIR / "sample_ci.yml", event="push")
        wf_pr = analyzer.analyze(FIXTURES_DIR / "sample_ci.yml", event="pull_request")
        assert wf_no_event.total_jobs == wf_push.total_jobs == wf_pr.total_jobs
        assert set(wf_no_event.jobs) == set(wf_push.jobs) == set(wf_pr.jobs)


# =====================================================================
# Matrix entry parsing
# =====================================================================


class TestMatrixEntryParsing:
    """Individual matrix entry parsing."""

    def test_gcc15_entry(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[4]  # GCC 15
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
        assert gcc.is_latest is True

    def test_clang20_entry(self, sample_workflow):
        clang = sample_workflow.jobs["build"].matrix[8]  # Clang 20
        assert clang.name == "Clang 20: C++20-23"
        assert clang.compiler.family == CompilerFamily.CLANG
        assert clang.compiler.cxxstd == ["20", "23"]
        assert clang.compiler.latest_cxxstd == "23"
        assert clang.platform == Platform.LINUX
        assert clang.container.image == "ubuntu:24.04"

    def test_msvc_entry(self, sample_workflow):
        msvc = sample_workflow.jobs["build"].matrix[0]  # MSVC 14.42
        assert msvc.name == "MSVC 14.42: C++20"
        assert msvc.compiler.family == CompilerFamily.MSVC
        assert msvc.compiler.version == "14.42"
        assert msvc.platform == Platform.WINDOWS
        assert msvc.container.image is None
        assert msvc.generator == "Visual Studio 17 2022"
        assert msvc.is_latest is True

    def test_msvc_shared_entry(self, sample_workflow):
        msvc2 = sample_workflow.jobs["build"].matrix[1]  # MSVC 14.34
        assert msvc2.name == "MSVC 14.34: C++20 (shared)"
        assert msvc2.variant.shared is True
        assert msvc2.is_earliest is True

    def test_mingw_entry(self, sample_workflow):
        mingw = sample_workflow.jobs["build"].matrix[2]
        assert mingw.name == "MinGW: C++20"
        assert mingw.compiler.family == CompilerFamily.MINGW
        assert mingw.compiler.is_wildcard is True
        assert mingw.platform == Platform.WINDOWS

    def test_apple_clang_entry(self, sample_workflow):
        ac = sample_workflow.jobs["build"].matrix[3]
        assert ac.name == "Apple-Clang: asan+ubsan"
        assert ac.compiler.family == CompilerFamily.APPLE_CLANG
        assert ac.platform == Platform.MACOS
        assert ac.variant.asan is True
        assert ac.variant.ubsan is True

    def test_coverage_entry(self, sample_workflow):
        cov = sample_workflow.jobs["build"].matrix[7]  # GCC 13 coverage
        assert cov.name == "GCC 13: C++20 coverage"
        assert cov.variant.coverage is True
        assert cov.compiler.family == CompilerFamily.GCC
        assert cov.compiler.version == "13"

    def test_x86_entry(self, sample_workflow):
        x86 = sample_workflow.jobs["build"].matrix[11]  # Clang 20 x86
        assert x86.name == "Clang 20: C++20-23 x86"
        assert x86.variant.x86 is True
        assert x86.architecture == "x86"


# =====================================================================
# Platform classification -- acceptance criteria
# =====================================================================


class TestPlatformClassification:
    """Platform detection -- spec requires 10 Linux, 3 Windows, 1 macOS."""

    def test_platform_summary(self, sample_workflow):
        summary = sample_workflow.platform_summary()
        assert summary[Platform.LINUX] == 10
        assert summary[Platform.WINDOWS] == 3
        assert summary[Platform.MACOS] == 1

    def test_filter_linux(self, analyzer, sample_workflow):
        linux = analyzer.filter_by_platform(sample_workflow, Platform.LINUX)
        assert len(linux) == 10
        assert all(e.platform == Platform.LINUX for e in linux)

    def test_filter_windows(self, analyzer, sample_workflow):
        win = analyzer.filter_by_platform(sample_workflow, Platform.WINDOWS)
        assert len(win) == 3

    def test_filter_macos(self, analyzer, sample_workflow):
        mac = analyzer.filter_by_platform(sample_workflow, Platform.MACOS)
        assert len(mac) == 1
        assert mac[0].compiler.family == CompilerFamily.APPLE_CLANG


# =====================================================================
# Compiler classification -- acceptance criteria
# =====================================================================


class TestCompilerClassification:
    """Compiler detection -- spec requires 4 GCC entries."""

    def test_filter_gcc(self, analyzer, sample_workflow):
        gcc = analyzer.filter_by_compiler(sample_workflow, CompilerFamily.GCC)
        assert len(gcc) == 4  # GCC 15, GCC 15 asan, GCC 12, GCC 13 coverage

    def test_filter_clang(self, analyzer, sample_workflow):
        clang = analyzer.filter_by_compiler(sample_workflow, CompilerFamily.CLANG)
        assert len(clang) == 6

    def test_filter_msvc(self, analyzer, sample_workflow):
        msvc = analyzer.filter_by_compiler(sample_workflow, CompilerFamily.MSVC)
        assert len(msvc) == 2

    def test_filter_apple_clang(self, analyzer, sample_workflow):
        ac = analyzer.filter_by_compiler(
            sample_workflow, CompilerFamily.APPLE_CLANG
        )
        assert len(ac) == 1

    def test_filter_mingw(self, analyzer, sample_workflow):
        mingw = analyzer.filter_by_compiler(
            sample_workflow, CompilerFamily.MINGW
        )
        assert len(mingw) == 1

    def test_compiler_display_name(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[4]
        assert gcc.compiler.display_name == "GCC 15"
        msvc = sample_workflow.jobs["build"].matrix[0]
        assert msvc.compiler.display_name == "MSVC 14.42"


# =====================================================================
# Build system detection
# =====================================================================


class TestBuildSystemDetection:
    """Build system detection from matrix flags and steps."""

    def test_b2_and_cmake(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[4]  # GCC 15 build-cmake
        assert gcc.build_system == BuildSystem.BOTH

    def test_coverage_is_cmake(self, sample_workflow):
        cov = sample_workflow.jobs["build"].matrix[7]  # GCC 13 coverage
        assert cov.build_system == BuildSystem.CMAKE

    def test_plain_b2(self, sample_workflow):
        asan = sample_workflow.jobs["build"].matrix[5]  # GCC 15 asan
        assert asan.build_system == BuildSystem.B2


# =====================================================================
# Variant filtering
# =====================================================================


class TestVariantFiltering:
    """Variant-based filtering."""

    def test_filter_asan(self, analyzer, sample_workflow):
        asan = analyzer.filter_by_variant(sample_workflow, asan=True)
        assert len(asan) == 3  # apple-clang, gcc15 asan, clang20 asan

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

    def test_time_trace_label(self):
        v = BuildVariant(time_trace=True)
        assert "time-trace" in v.label

    def test_valgrind_label(self):
        v = BuildVariant(valgrind=True)
        assert "valgrind" in v.label


# =====================================================================
# Search
# =====================================================================


class TestSearch:
    """Search by name pattern."""

    def test_search_gcc(self, analyzer, sample_workflow):
        results = analyzer.search(sample_workflow, "gcc")
        assert len(results) == 4

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
        gcc = sample_workflow.jobs["build"].matrix[4]
        assert gcc.container.os_name == "ubuntu"
        assert gcc.container.os_version == "25.04"

    def test_no_container(self, sample_workflow):
        msvc = sample_workflow.jobs["build"].matrix[0]
        assert msvc.container.image is None
        assert msvc.is_containerized is False

    def test_is_containerized(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[4]
        assert gcc.is_containerized is True


# =====================================================================
# Package requirements
# =====================================================================


class TestPackageRequirements:
    """Package requirements extraction."""

    def test_gcc_packages(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[4]
        assert "gcc-15" in gcc.packages.apt_packages
        assert "g++-15" in gcc.packages.apt_packages
        assert "libssl-dev" in gcc.packages.apt_packages

    def test_cmake_build_tool(self, sample_workflow):
        gcc = sample_workflow.jobs["build"].matrix[4]
        assert "cmake" in gcc.packages.build_tools

    def test_coverage_packages(self, sample_workflow):
        cov = sample_workflow.jobs["build"].matrix[7]
        assert "lcov" in cov.packages.apt_packages

    def test_x86_architecture(self, sample_workflow):
        x86 = sample_workflow.jobs["build"].matrix[11]
        assert x86.packages.apt_add_architecture == "i386"

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
        assert s["platform_summary"]["linux"] == 10
        assert s["platform_summary"]["windows"] == 3
        assert s["platform_summary"]["macos"] == 1
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

    def test_compiler_latest_cxxstd(self):
        ci = CompilerInfo(
            family=CompilerFamily.CLANG,
            version="20",
            latest_cxxstd="23",
        )
        assert ci.latest_cxxstd == "23"

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

    def test_matrix_entry_is_latest_earliest(self):
        entry = MatrixEntry(
            index=0,
            name="test",
            platform=Platform.LINUX,
            compiler=CompilerInfo(family=CompilerFamily.GCC, version="15"),
            container=ContainerInfo(),
            variant=BuildVariant(),
            packages=PackageRequirements(),
            runs_on="ubuntu-latest",
            build_system=BuildSystem.B2,
            is_latest=True,
            is_earliest=False,
        )
        assert entry.is_latest is True
        assert entry.is_earliest is False

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
# Error classes
# =====================================================================


class TestErrorClasses:
    """Error hierarchy and messages."""

    def test_workflow_parse_error(self):
        err = WorkflowParseError(Path("ci.yml"), "bad yaml")
        assert "ci.yml" in str(err)
        assert isinstance(err, WorkflowError)

    def test_unsupported_matrix_error(self):
        err = UnsupportedMatrixError({"name": "test"}, "missing field")
        assert "test" in str(err)
        assert isinstance(err, WorkflowError)

    def test_missing_field_error(self):
        err = MissingFieldError("compiler", "matrix entry 3")
        assert "compiler" in str(err)
        assert isinstance(err, WorkflowError)
# Event filtering
# =====================================================================


class TestEventFiltering:
    """--event filter: only jobs that run on the given event."""

    def test_job_runs_on_event_no_condition(self):
        """Job with no condition runs on any event."""
        job = Job(
            id="build",
            name="Build",
            runs_on="ubuntu-latest",
            condition=None,
        )
        assert WorkflowAnalyzer._job_runs_on_event(job, "push") is True
        assert WorkflowAnalyzer._job_runs_on_event(job, "pull_request") is True

    def test_job_runs_on_event_equals_push(self):
        """Job with if: github.event_name == 'push' runs only on push."""
        job = Job(
            id="build",
            name="Build",
            runs_on="ubuntu-latest",
            condition="github.event_name == 'push'",
        )
        assert WorkflowAnalyzer._job_runs_on_event(job, "push") is True
        assert WorkflowAnalyzer._job_runs_on_event(job, "pull_request") is False

    def test_job_runs_on_event_equals_pull_request(self):
        """Job with if: github.event_name == 'pull_request' runs only on pull_request."""
        job = Job(
            id="changelog",
            name="Changelog",
            runs_on="ubuntu-latest",
            condition='github.event_name == "pull_request"',
        )
        assert WorkflowAnalyzer._job_runs_on_event(job, "pull_request") is True
        assert WorkflowAnalyzer._job_runs_on_event(job, "push") is False

    def test_job_runs_on_event_not_equals(self):
        """Job with if: github.event_name != 'schedule' runs on push, not on schedule."""
        job = Job(
            id="build",
            name="Build",
            runs_on="ubuntu-latest",
            condition="github.event_name != 'schedule'",
        )
        assert WorkflowAnalyzer._job_runs_on_event(job, "push") is True
        assert WorkflowAnalyzer._job_runs_on_event(job, "schedule") is False

    def test_job_runs_on_event_condition_without_event_name(self):
        """Job with if that doesn't mention event_name runs on any event."""
        job = Job(
            id="build",
            name="Build",
            runs_on="ubuntu-latest",
            condition="github.ref == 'refs/heads/main'",
        )
        assert WorkflowAnalyzer._job_runs_on_event(job, "push") is True
        assert WorkflowAnalyzer._job_runs_on_event(job, "pull_request") is True


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

    def test_analyze_sample_workflow_fixture(self, analyzer):
        """Also parse the original sample_workflow.yml (Issue 1 fixture)."""
        fixture = FIXTURES_DIR / "sample_workflow.yml"
        if fixture.exists():
            wf = analyzer.analyze(fixture)
            assert wf.total_jobs >= 1
