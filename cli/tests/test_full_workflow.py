"""Integration tests for the full capy CI workflow (Issue 2 acceptance criteria).

Uses the ``sample_workflow.yml`` fixture which contains all 14 matrix
configurations from capy's CI:
    - 3 Windows (MSVC 14.42, MSVC 14.34 shared, MinGW)
    - 1 macOS   (Apple-Clang asan+ubsan)
    - 10 Linux  (GCC 12/13/15 variants, Clang 17/20 variants)

These tests verify the acceptance criteria from Issue #2:
    1. Parse all 14 matrix entries correctly
    2. Classify by platform (10 Linux, 3 Windows, 1 macOS)
    3. Detect compiler family and version for all entries
    4. Detect container image for containerized entries
    5. Detect build system (B2, CMake, or both) per entry
    6. Extract package requirements
    7. Parse job dependencies
    8. Filter by platform, compiler, variant
    9. Search by name pattern
    10. JSON output matches expected schema
    11. Handle multiple workflow files
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
from localci.core.serialization import (
    workflow_summary,
    workflow_to_dict,
    workflow_to_json,
)
from localci.core.workflow import (
    BuildSystem,
    CompilerFamily,
    Platform,
    WorkflowAnalyzer,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FULL_WORKFLOW = FIXTURES_DIR / "sample_workflow.yml"


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def analyzer():
    return WorkflowAnalyzer()


@pytest.fixture
def workflow(analyzer):
    return analyzer.analyze(FULL_WORKFLOW)


# =====================================================================
# AC-1: Parse all 14 matrix entries correctly
# =====================================================================


class TestParseAll14Entries:
    """Acceptance criterion 1: extract all 14 matrix entries."""

    def test_workflow_name(self, workflow):
        assert workflow.name == "CI"

    def test_total_jobs(self, workflow):
        assert workflow.total_jobs == 2
        assert "build" in workflow.jobs
        assert "changelog" in workflow.jobs

    def test_build_has_14_matrix_entries(self, workflow):
        build = workflow.jobs["build"]
        assert len(build.matrix) == 14

    def test_total_matrix_entries_includes_changelog(self, workflow):
        # 14 from build + 1 from changelog (no matrix = 1 config)
        assert workflow.total_matrix_entries == 15

    def test_all_entry_names(self, workflow):
        names = [e.name for e in workflow.jobs["build"].matrix]
        expected_names = [
            "MSVC 14.42: C++20",
            "MSVC 14.34: C++20 shared",
            "MinGW: C++20",
            "Apple-Clang: asan+ubsan",
            "GCC 15: C++20",
            "GCC 15: C++20 asan+ubsan",
            "GCC 12: C++20",
            "GCC 13: C++20 coverage",
            "Clang 20: C++20-23",
            "Clang 20: C++20 asan+ubsan",
            "Clang 17: C++20",
            "Clang 20: C++20-23 x86",
            "GCC 15: C++20 valgrind",
            "GCC 13: C++20 shared",
        ]
        assert names == expected_names

    def test_events(self, workflow):
        assert "push" in workflow.events
        assert "pull_request" in workflow.events

    def test_global_env(self, workflow):
        assert workflow.env["GIT_FETCH_JOBS"] == "8"
        assert workflow.env["NET_RETRY_COUNT"] == "5"
        assert workflow.env["DEBIAN_FRONTEND"] == "noninteractive"

    def test_concurrency(self, workflow):
        assert workflow.concurrency is not None
        assert workflow.concurrency["cancel-in-progress"] is True


# =====================================================================
# AC-2: Classify by platform (10 Linux, 3 Windows, 1 macOS)
# =====================================================================


class TestPlatformClassification:
    """Acceptance criterion 2: platform counts match capy's breakdown."""

    def test_platform_summary(self, workflow):
        summary = workflow.platform_summary()
        assert summary[Platform.LINUX] == 10
        assert summary[Platform.WINDOWS] == 3
        assert summary[Platform.MACOS] == 1

    def test_filter_linux_count(self, analyzer, workflow):
        linux = analyzer.filter_by_platform(workflow, Platform.LINUX)
        assert len(linux) == 10

    def test_filter_windows_count(self, analyzer, workflow):
        win = analyzer.filter_by_platform(workflow, Platform.WINDOWS)
        assert len(win) == 3

    def test_filter_macos_count(self, analyzer, workflow):
        mac = analyzer.filter_by_platform(workflow, Platform.MACOS)
        assert len(mac) == 1

    def test_no_unknown_platform(self, workflow):
        for entry in workflow.all_matrix_entries():
            assert entry.platform != Platform.UNKNOWN, (
                f"Entry '{entry.name}' has UNKNOWN platform"
            )


# =====================================================================
# AC-3: Detect compiler family and version for all entries
# =====================================================================


class TestCompilerDetection:
    """Acceptance criterion 3: correct compiler for every entry."""

    def test_gcc_entries(self, analyzer, workflow):
        gcc = analyzer.filter_by_compiler(workflow, CompilerFamily.GCC)
        assert len(gcc) == 6  # GCC 15, GCC 15 asan, GCC 12, GCC 13 cov, GCC 15 valgrind, GCC 13 shared

    def test_clang_entries(self, analyzer, workflow):
        clang = analyzer.filter_by_compiler(workflow, CompilerFamily.CLANG)
        assert len(clang) == 4  # Clang 20, Clang 20 asan, Clang 17, Clang 20 x86

    def test_msvc_entries(self, analyzer, workflow):
        msvc = analyzer.filter_by_compiler(workflow, CompilerFamily.MSVC)
        assert len(msvc) == 2  # MSVC 14.42, MSVC 14.34

    def test_mingw_entries(self, analyzer, workflow):
        mingw = analyzer.filter_by_compiler(workflow, CompilerFamily.MINGW)
        assert len(mingw) == 1

    def test_apple_clang_entries(self, analyzer, workflow):
        ac = analyzer.filter_by_compiler(workflow, CompilerFamily.APPLE_CLANG)
        assert len(ac) == 1

    def test_msvc_1442_version(self, workflow):
        msvc = workflow.jobs["build"].matrix[0]
        assert msvc.compiler.family == CompilerFamily.MSVC
        assert msvc.compiler.version == "14.42"
        assert msvc.compiler.display_name == "MSVC 14.42"

    def test_msvc_1434_version(self, workflow):
        msvc34 = workflow.jobs["build"].matrix[1]
        assert msvc34.compiler.family == CompilerFamily.MSVC
        assert msvc34.compiler.version == "14.34"

    def test_mingw_wildcard_version(self, workflow):
        mingw = workflow.jobs["build"].matrix[2]
        assert mingw.compiler.family == CompilerFamily.MINGW
        assert mingw.compiler.is_wildcard

    def test_gcc_cc_and_cxx(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        assert gcc15.compiler.cc == "gcc-15"
        assert gcc15.compiler.cxx == "g++-15"
        assert gcc15.compiler.b2_toolset == "gcc"

    def test_clang_cc_and_cxx(self, workflow):
        clang20 = workflow.jobs["build"].matrix[8]
        assert clang20.compiler.cc == "clang-20"
        assert clang20.compiler.cxx == "clang++-20"
        assert clang20.compiler.b2_toolset == "clang"

    def test_cxxstd_multi(self, workflow):
        clang20 = workflow.jobs["build"].matrix[8]
        assert clang20.compiler.cxxstd == ["20", "23"]


# =====================================================================
# AC-4: Detect container for containerized entries
# =====================================================================


class TestContainerDetection:
    """Acceptance criterion 4: container image parsing."""

    def test_gcc15_container(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        assert gcc15.container.image == "ubuntu:25.04"
        assert gcc15.container.os_name == "ubuntu"
        assert gcc15.container.os_version == "25.04"
        assert gcc15.is_containerized is True

    def test_gcc12_container(self, workflow):
        gcc12 = workflow.jobs["build"].matrix[6]
        assert gcc12.container.image == "ubuntu:22.04"

    def test_clang20_container(self, workflow):
        clang20 = workflow.jobs["build"].matrix[8]
        assert clang20.container.image == "ubuntu:24.04"

    def test_msvc_no_container(self, workflow):
        msvc = workflow.jobs["build"].matrix[0]
        assert msvc.container.image is None
        assert msvc.is_containerized is False

    def test_macos_no_container(self, workflow):
        ac = workflow.jobs["build"].matrix[3]
        assert ac.container.image is None
        assert ac.is_containerized is False

    def test_coverage_no_container(self, workflow):
        cov = workflow.jobs["build"].matrix[7]
        # GCC 13 coverage runs on ubuntu-24.04 runner, no container
        assert cov.container.image is None

    def test_containerized_count(self, workflow):
        entries = workflow.jobs["build"].matrix
        containerized = [e for e in entries if e.is_containerized]
        non_containerized = [e for e in entries if not e.is_containerized]
        # Containerized: GCC 15, GCC 15 asan, GCC 12, Clang 20, Clang 20 asan,
        #   Clang 20 x86, GCC 15 valgrind, GCC 13 shared = 8
        assert len(containerized) == 8
        # Non-containerized: MSVC 14.42, MSVC 14.34, MinGW, Apple-Clang,
        #   GCC 13 coverage, Clang 17 = 6
        assert len(non_containerized) == 6


# =====================================================================
# AC-5: Detect build system (B2, CMake, or both)
# =====================================================================


class TestBuildSystemDetection:
    """Acceptance criterion 5: build system detection."""

    def test_gcc15_both(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        # build-cmake=true + B2 step applies
        assert gcc15.build_system == BuildSystem.BOTH

    def test_msvc1442_both(self, workflow):
        msvc = workflow.jobs["build"].matrix[0]
        # build-cmake=true + B2 step
        assert msvc.build_system == BuildSystem.BOTH

    def test_coverage_cmake(self, workflow):
        cov = workflow.jobs["build"].matrix[7]
        # Coverage builds skip B2, run CMake
        assert cov.build_system == BuildSystem.CMAKE

    def test_asan_b2_only(self, workflow):
        asan = workflow.jobs["build"].matrix[5]
        # GCC 15 asan: no build-cmake flag, no coverage
        assert asan.build_system == BuildSystem.B2

    def test_clang17_b2_only(self, workflow):
        clang17 = workflow.jobs["build"].matrix[10]
        assert clang17.build_system == BuildSystem.B2

    def test_mingw_both(self, workflow):
        mingw = workflow.jobs["build"].matrix[2]
        # build-cmake=true
        assert mingw.build_system == BuildSystem.BOTH


# =====================================================================
# AC-6: Extract package requirements
# =====================================================================


class TestPackageRequirements:
    """Acceptance criterion 6: package requirement extraction."""

    def test_gcc15_packages(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        assert "gcc-15" in gcc15.packages.apt_packages
        assert "g++-15" in gcc15.packages.apt_packages
        assert "libssl-dev" in gcc15.packages.apt_packages
        assert "zlib1g-dev" in gcc15.packages.apt_packages

    def test_coverage_packages(self, workflow):
        cov = workflow.jobs["build"].matrix[7]
        assert "lcov" in cov.packages.apt_packages
        assert "wget" in cov.packages.apt_packages

    def test_coverage_cxxflags(self, workflow):
        cov = workflow.jobs["build"].matrix[7]
        assert cov.packages.cxxflags is not None
        assert "--coverage" in cov.packages.cxxflags

    def test_x86_architecture(self, workflow):
        x86 = workflow.jobs["build"].matrix[11]
        assert x86.architecture == "x86"
        assert x86.packages.apt_add_architecture == "i386"
        assert "gcc-multilib" in x86.packages.apt_packages

    def test_build_cmake_tool(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        assert "cmake" in gcc15.packages.build_tools

    def test_valgrind_packages(self, workflow):
        valgrind = workflow.jobs["build"].matrix[12]
        assert "valgrind" in valgrind.packages.apt_packages

    def test_clang_packages(self, workflow):
        clang20 = workflow.jobs["build"].matrix[8]
        assert "clang-20" in clang20.packages.apt_packages


# =====================================================================
# AC-7: Parse job dependencies
# =====================================================================


class TestJobDependencies:
    """Acceptance criterion 7: job dependency ordering."""

    def test_changelog_depends_on_build(self, workflow):
        changelog = workflow.jobs["changelog"]
        assert "build" in changelog.needs

    def test_build_no_dependencies(self, workflow):
        build = workflow.jobs["build"]
        assert build.needs == []

    def test_dependency_order(self, workflow):
        order = workflow.dependency_order()
        assert order.index("build") < order.index("changelog")

    def test_dependency_order_includes_all_jobs(self, workflow):
        order = workflow.dependency_order()
        assert set(order) == set(workflow.jobs.keys())


# =====================================================================
# AC-8: Filter by platform, compiler, variant
# =====================================================================


class TestFiltering:
    """Acceptance criterion 8: comprehensive filtering."""

    def test_filter_asan(self, analyzer, workflow):
        asan = analyzer.filter_by_variant(workflow, asan=True)
        # GCC 15 asan, Clang 20 asan, Apple-Clang asan
        assert len(asan) == 3
        for e in asan:
            assert e.variant.asan is True

    def test_filter_ubsan(self, analyzer, workflow):
        ubsan = analyzer.filter_by_variant(workflow, ubsan=True)
        assert len(ubsan) == 3
        for e in ubsan:
            assert e.variant.ubsan is True

    def test_filter_coverage(self, analyzer, workflow):
        cov = analyzer.filter_by_variant(workflow, coverage=True)
        assert len(cov) == 1
        assert cov[0].name == "GCC 13: C++20 coverage"

    def test_filter_shared(self, analyzer, workflow):
        shared = analyzer.filter_by_variant(workflow, shared=True)
        # GCC 15, GCC 12, Clang 20, MSVC 14.34, GCC 13 shared
        assert len(shared) >= 4

    def test_filter_x86(self, analyzer, workflow):
        x86 = analyzer.filter_by_variant(workflow, x86=True)
        assert len(x86) == 1
        assert "x86" in x86[0].name.lower()

    def test_filter_valgrind(self, analyzer, workflow):
        valgrind = analyzer.filter_by_variant(workflow, valgrind=True)
        assert len(valgrind) == 1
        assert "valgrind" in valgrind[0].name.lower()

    def test_combined_filter_linux_gcc(self, analyzer, workflow):
        gcc = analyzer.filter_by_compiler(workflow, CompilerFamily.GCC)
        linux_gcc = [e for e in gcc if e.platform == Platform.LINUX]
        assert len(linux_gcc) == 6


# =====================================================================
# AC-9: Search by name pattern
# =====================================================================


class TestSearchByName:
    """Acceptance criterion 9: name-based search."""

    def test_search_gcc(self, analyzer, workflow):
        results = analyzer.search(workflow, "gcc")
        assert len(results) == 6

    def test_search_clang(self, analyzer, workflow):
        results = analyzer.search(workflow, "clang")
        # 4 Clang + 1 Apple-Clang
        assert len(results) == 5

    def test_search_msvc(self, analyzer, workflow):
        results = analyzer.search(workflow, "msvc")
        assert len(results) == 2

    def test_search_asan(self, analyzer, workflow):
        results = analyzer.search(workflow, "asan")
        assert len(results) == 3

    def test_search_coverage(self, analyzer, workflow):
        results = analyzer.search(workflow, "coverage")
        assert len(results) == 1

    def test_search_x86(self, analyzer, workflow):
        results = analyzer.search(workflow, "x86")
        assert len(results) == 1

    def test_search_case_insensitive(self, analyzer, workflow):
        upper = analyzer.search(workflow, "GCC")
        lower = analyzer.search(workflow, "gcc")
        assert len(upper) == len(lower)

    def test_search_no_match(self, analyzer, workflow):
        results = analyzer.search(workflow, "nonexistent_compiler")
        assert len(results) == 0


# =====================================================================
# AC-10: JSON output matches expected schema
# =====================================================================


class TestJsonOutput:
    """Acceptance criterion 10: valid JSON output."""

    def test_to_json_parses(self, workflow):
        j = workflow_to_json(workflow)
        data = json.loads(j)
        assert isinstance(data, dict)

    def test_json_has_required_fields(self, workflow):
        data = json.loads(workflow_to_json(workflow))
        assert data["name"] == "CI"
        assert "events" in data
        assert "jobs" in data
        assert "env" in data

    def test_json_jobs_structure(self, workflow):
        data = json.loads(workflow_to_json(workflow))
        build = data["jobs"]["build"]
        assert "matrix" in build
        assert len(build["matrix"]) == 14

    def test_json_matrix_entry_fields(self, workflow):
        data = json.loads(workflow_to_json(workflow))
        entry = data["jobs"]["build"]["matrix"][0]
        required_fields = [
            "index", "name", "platform", "compiler", "container",
            "variant", "packages", "runs_on", "build_system",
            "architecture",
        ]
        for field in required_fields:
            assert field in entry, f"Missing field '{field}' in JSON matrix entry"

    def test_json_compiler_structure(self, workflow):
        data = json.loads(workflow_to_json(workflow))
        compiler = data["jobs"]["build"]["matrix"][0]["compiler"]
        assert "family" in compiler
        assert "version" in compiler
        assert compiler["family"] == "msvc"
        assert compiler["version"] == "14.42"

    def test_to_dict(self, workflow):
        d = workflow_to_dict(workflow)
        assert isinstance(d, dict)
        assert d["name"] == "CI"

    def test_summary(self, workflow):
        s = workflow_summary(workflow)
        assert s["name"] == "CI"
        assert s["total_jobs"] == 2
        assert s["total_matrix_entries"] == 15
        assert s["platform_summary"]["linux"] == 10
        assert s["platform_summary"]["windows"] == 3
        assert s["platform_summary"]["macos"] == 1
        assert "dependency_order" in s


# =====================================================================
# AC-11: Handle multiple workflow files
# =====================================================================


class TestMultipleWorkflows:
    """Acceptance criterion 11: analyze_multiple."""

    def test_analyze_fixtures_dir(self, analyzer):
        workflows = analyzer.analyze_multiple(FIXTURES_DIR)
        assert len(workflows) == 2  # sample_ci.yml + sample_workflow.yml

    def test_each_workflow_valid(self, analyzer):
        workflows = analyzer.analyze_multiple(FIXTURES_DIR)
        for wf in workflows:
            assert wf.name is not None
            assert wf.total_jobs >= 1


# =====================================================================
# Steps parsing (full workflow)
# =====================================================================


class TestStepsParsing:
    """Step-level analysis for build system detection."""

    def test_build_step_count(self, workflow):
        build = workflow.jobs["build"]
        assert len(build.steps) == 10  # All 10 capy steps

    def test_checkout_step(self, workflow):
        step = workflow.jobs["build"].steps[0]
        assert step.action_name == "checkout"
        assert step.is_action is True

    def test_b2_step(self, workflow):
        step = workflow.jobs["build"].steps[6]
        assert step.action_name == "b2-workflow"
        assert step.condition is not None

    def test_cmake_step(self, workflow):
        step = workflow.jobs["build"].steps[7]
        assert step.action_name == "cmake-workflow"
        assert step.condition is not None

    def test_run_step(self, workflow):
        step = workflow.jobs["build"].steps[4]  # ASLR fix
        assert step.name == "ASLR Fix"
        assert step.run is not None
        assert step.is_action is False

    def test_changelog_steps(self, workflow):
        changelog = workflow.jobs["changelog"]
        assert len(changelog.steps) == 2


# =====================================================================
# Variant detail tests
# =====================================================================


class TestVariantDetails:
    """Detailed variant classification."""

    def test_asan_ubsan_label(self, workflow):
        ac = workflow.jobs["build"].matrix[3]
        assert "asan" in ac.variant.label
        assert "ubsan" in ac.variant.label

    def test_shared_label(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        assert "shared" in gcc15.variant.label

    def test_coverage_label(self, workflow):
        cov = workflow.jobs["build"].matrix[7]
        assert "coverage" in cov.variant.label

    def test_x86_label(self, workflow):
        x86 = workflow.jobs["build"].matrix[11]
        assert "x86" in x86.variant.label

    def test_sanitizers_list(self, workflow):
        ac = workflow.jobs["build"].matrix[3]
        assert ac.variant.sanitizers == ["asan", "ubsan"]

    def test_standard_variant(self, workflow):
        # Clang 17 has no special flags
        clang17 = workflow.jobs["build"].matrix[10]
        assert clang17.variant.label == "standard"


# =====================================================================
# Image requirements key
# =====================================================================


class TestImageRequirementsKey:
    """Image matching key generation."""

    def test_containerized_key(self, workflow):
        gcc15 = workflow.jobs["build"].matrix[4]
        key = gcc15.image_requirements_key
        assert "ubuntu:25.04" in key
        assert "gcc" in key
        assert "15" in key

    def test_runner_key(self, workflow):
        msvc = workflow.jobs["build"].matrix[0]
        key = msvc.image_requirements_key
        assert "windows-2022" in key
        assert "msvc" in key

    def test_x86_key(self, workflow):
        x86 = workflow.jobs["build"].matrix[11]
        key = x86.image_requirements_key
        assert "x86" in key


# =====================================================================
# CLI integration (end-to-end)
# =====================================================================


class TestCLIIntegration:
    """End-to-end CLI tests with the full workflow fixture."""

    def test_analyze_table(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze", str(FULL_WORKFLOW)])
        assert result.exit_code == 0
        assert "MSVC 14.42" in result.output
        assert "GCC 15" in result.output

    def test_analyze_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze", str(FULL_WORKFLOW), "-f", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "CI"

    def test_list_all(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "-w", str(FULL_WORKFLOW)])
        assert result.exit_code == 0

    def test_list_platform_linux(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["list", "-w", str(FULL_WORKFLOW), "-p", "linux"]
        )
        assert result.exit_code == 0

    def test_list_platform_windows(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["list", "-w", str(FULL_WORKFLOW), "-p", "windows"]
        )
        assert result.exit_code == 0

    def test_list_compiler_gcc(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["list", "-w", str(FULL_WORKFLOW), "--compiler", "gcc"]
        )
        assert result.exit_code == 0

    def test_list_json(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["list", "-w", str(FULL_WORKFLOW), "-f", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 14

    def test_analyze_output_to_file(self, tmp_path):
        runner = CliRunner()
        out = tmp_path / "output.json"
        result = runner.invoke(
            cli, ["analyze", str(FULL_WORKFLOW), "-f", "json", "-o", str(out)]
        )
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["name"] == "CI"
