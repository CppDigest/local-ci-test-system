"""Tests for the job executor module (Issue #3).

Covers:
- ActCommand construction and flag generation
- JobResult properties and display
- JobExecutor preflight checks and execution
- ActCommandBuilder translation from MatrixEntry
- DockerManager operations (mocked)
- ExecutionSummary aggregation, serialisation, and loading
- CLI integration for ``localci run``, ``localci status``, ``localci logs``
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localci.core.command_builder import ActCommandBuilder
from localci.core.executor import (
    ActCommand,
    ActNotFoundError,
    DockerNotAvailableError,
    JobExecutor,
    JobResult,
    JobStatus,
)
from localci.core.results import ExecutionSummary
from localci.core.workflow import (
    BuildSystem,
    BuildVariant,
    CompilerFamily,
    CompilerInfo,
    ContainerInfo,
    MatrixEntry,
    PackageRequirements,
    Platform,
)

# =====================================================================
# Fixtures
# =====================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_entry(
    *,
    index: int = 0,
    name: str = "GCC 15: C++20",
    platform: Platform = Platform.LINUX,
    compiler_family: CompilerFamily = CompilerFamily.GCC,
    compiler_version: str = "15",
    cc: str = "gcc-15",
    cxx: str = "g++-15",
    container_image: str | None = "ubuntu:25.04",
    runs_on: str = "ubuntu-latest",
    architecture: str = "x86_64",
    raw: dict | None = None,
) -> MatrixEntry:
    """Create a minimal MatrixEntry for testing."""
    return MatrixEntry(
        index=index,
        name=name,
        platform=platform,
        compiler=CompilerInfo(
            family=compiler_family,
            version=compiler_version,
            cc=cc,
            cxx=cxx,
        ),
        container=ContainerInfo(image=container_image),
        variant=BuildVariant(),
        packages=PackageRequirements(),
        runs_on=runs_on,
        build_system=BuildSystem.B2,
        architecture=architecture,
        raw=raw or {"name": name, "compiler": "gcc", "version": "15"},
    )


def _make_summary(
    num_passed: int = 3,
    num_failed: int = 1,
) -> ExecutionSummary:
    """Create a summary with a mix of results."""
    summary = ExecutionSummary(
        execution_id="abc12345",
        started_at=datetime(2026, 2, 10, 10, 0, 0),
    )
    for i in range(num_passed):
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=i,
                matrix_name=f"Job {i}",
                status=JobStatus.PASSED,
                exit_code=0,
                duration_seconds=30.0 + i * 5,
                started_at=datetime(2026, 2, 10, 10, 0, 0),
                finished_at=datetime(2026, 2, 10, 10, 0, 30 + i * 5),
            )
        )
    for i in range(num_failed):
        idx = num_passed + i
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=idx,
                matrix_name=f"Failed Job {idx}",
                status=JobStatus.FAILED,
                exit_code=1,
                duration_seconds=45.0,
                error_message="compilation failed",
                started_at=datetime(2026, 2, 10, 10, 0, 0),
                finished_at=datetime(2026, 2, 10, 10, 0, 45),
            )
        )
    summary.finished_at = datetime(2026, 2, 10, 10, 5, 0)
    return summary


# =====================================================================
# ActCommand tests
# =====================================================================


class TestActCommand:
    """Test act command construction."""

    def test_basic_command(self):
        cmd = ActCommand(
            workflow_file=Path(".github/workflows/ci.yml"),
            job_id="build",
        )
        args = cmd.build()
        assert args[0] == "act"
        assert "-W" in args
        # Path conversion is platform-specific (forward slash on Unix, backslash on Windows)
        assert any(".github" in arg and "ci.yml" in arg for arg in args)
        assert "-j" in args
        assert "build" in args

    def test_act_cli_binary_name(self):
        """Test that act_binary field controls the command name."""
        cmd = ActCommand(
            workflow_file=Path(".github/workflows/ci.yml"),
            job_id="build",
            act_binary="act-cli",
        )
        args = cmd.build()
        assert args[0] == "act-cli"
        assert "-W" in args
        assert "-j" in args

    def test_matrix_filters(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            matrix_filters={"compiler": "gcc", "version": "15"},
        )
        args = cmd.build()
        assert "--matrix" in args
        assert "compiler:gcc" in args
        assert "version:15" in args

    def test_runner_mapping(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            runner_mappings={"ubuntu-latest": "my-image:latest"},
        )
        args = cmd.build()
        assert "-P" in args
        assert "ubuntu-latest=my-image:latest" in args

    def test_no_pull(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            pull=False,
        )
        args = cmd.build()
        assert "--pull=false" in args

    def test_pull_enabled_omits_flag(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            pull=True,
        )
        args = cmd.build()
        assert "--pull=false" not in args

    def test_offline_mode(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            offline=True,
        )
        args = cmd.build()
        assert "--action-offline-mode" in args

    def test_dryrun(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            dryrun=True,
        )
        args = cmd.build()
        assert "--dryrun" in args

    def test_verbose(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            verbose=True,
        )
        args = cmd.build()
        assert "-v" in args

    def test_privileged(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            privileged=True,
        )
        args = cmd.build()
        assert "--privileged" in args

    def test_rm(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            rm=True,
        )
        args = cmd.build()
        assert "--rm" in args

    def test_env_vars(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            env={"CC": "gcc-15", "CXX": "g++-15"},
        )
        args = cmd.build()
        assert "--env" in args
        assert "CC=gcc-15" in args
        assert "CXX=g++-15" in args

    def test_secrets(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            secrets={"GITHUB_TOKEN": "secret123"},
        )
        args = cmd.build()
        assert "--secret" in args
        assert "GITHUB_TOKEN=secret123" in args

    def test_display_redacts_secrets(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            secrets={"GITHUB_TOKEN": "secret123"},
        )
        display = cmd.display()
        assert "secret123" not in display
        assert "***" in display

    def test_container_architecture(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            container_architecture="linux/386",
        )
        args = cmd.build()
        assert "--container-architecture" in args
        assert "linux/386" in args

    def test_event_file(self, tmp_path):
        event = tmp_path / "event.json"
        event.write_text("{}")
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            event_file=event,
        )
        args = cmd.build()
        assert "-e" in args
        assert str(event) in args

    def test_env_file(self, tmp_path):
        env_f = tmp_path / ".env"
        env_f.write_text("KEY=val")
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            env_file=env_f,
        )
        args = cmd.build()
        assert "--env-file" in args
        assert str(env_f) in args

    def test_container_options(self):
        """Phase 2: --container-options for cache bind mounts."""
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            container_options="-v /host/ccache:/tmp/localci-cache/ccache",
        )
        args = cmd.build()
        assert "--container-options" in args
        assert "-v /host/ccache:/tmp/localci-cache/ccache" in args

    def test_str_returns_display(self):
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
        )
        assert str(cmd) == cmd.display()


# =====================================================================
# JobResult tests
# =====================================================================


class TestJobResult:
    """Test job result handling."""

    def test_success(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15: C++20",
            status=JobStatus.PASSED,
            exit_code=0,
            duration_seconds=45.2,
        )
        assert result.success is True
        assert result.status_icon == "✓"
        assert "45.2s" in result.duration_display

    def test_failure(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15: C++20",
            status=JobStatus.FAILED,
            exit_code=1,
            error_message="compilation failed",
        )
        assert result.success is False
        assert result.status_icon == "✗"

    def test_timeout(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15: C++20",
            status=JobStatus.TIMEOUT,
            duration_seconds=3600,
        )
        assert result.success is False
        assert "60m" in result.duration_display
        assert result.status_icon == "⏱"

    def test_error_status(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.ERROR,
            error_message="Docker not running",
        )
        assert result.success is False
        assert result.status_icon == "⚠"

    def test_cancelled_status(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.CANCELLED,
        )
        assert result.success is False
        assert result.status_icon == "⊘"

    def test_pending_status(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.PENDING,
        )
        assert result.status_icon == "◌"

    def test_summary_line(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15: C++20",
            status=JobStatus.PASSED,
            duration_seconds=30.5,
        )
        line = result.summary_line()
        assert "GCC 15" in line
        assert "passed" in line

    def test_duration_display_minutes(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="test",
            duration_seconds=125.0,
        )
        assert "2m" in result.duration_display

    def test_duration_display_seconds(self):
        result = JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="test",
            duration_seconds=9.3,
        )
        assert "9.3s" in result.duration_display

    def test_all_status_icons(self):
        """Every JobStatus value has a defined icon."""
        for status in JobStatus:
            r = JobResult(
                job_id="build",
                matrix_index=0,
                matrix_name="t",
                status=status,
            )
            assert r.status_icon != "?"

    def test_default_status_is_pending(self):
        r = JobResult(job_id="build", matrix_index=0, matrix_name="t")
        assert r.status == JobStatus.PENDING


# =====================================================================
# JobExecutor tests
# =====================================================================


class TestJobExecutor:
    """Test executor with mocked subprocess."""

    @pytest.fixture(autouse=True)
    def _logs_dir(self, tmp_path: Path) -> None:
        self.logs_dir = tmp_path / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    @patch("shutil.which")
    def test_has_act_true(self, mock_which):
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=self.logs_dir)
        assert executor.has_act is True

    @patch("shutil.which")
    def test_has_act_false(self, mock_which):
        mock_which.return_value = None
        executor = JobExecutor(logs_dir=self.logs_dir)
        assert executor.has_act is False

    @patch("sys.platform", "win32")
    @patch("shutil.which")
    def test_has_act_cli_windows(self, mock_which):
        """On Windows, should find act-cli.exe when act.exe is not available."""

        def which_side_effect(name):
            if name == "act":
                return None
            if name == "act-cli":
                return "C:\\ProgramData\\chocolatey\\bin\\act-cli.exe"
            return None

        mock_which.side_effect = which_side_effect
        executor = JobExecutor(logs_dir=self.logs_dir)
        assert executor.has_act is True
        assert executor._act_path == "C:\\ProgramData\\chocolatey\\bin\\act-cli.exe"

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_check_act_success(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/act"
        mock_run.return_value = MagicMock(returncode=0, stdout="act version 0.2.68")
        executor = JobExecutor(logs_dir=self.logs_dir)
        version = executor.check_act()
        assert "0.2.68" in version

    @patch("shutil.which")
    def test_check_act_not_installed(self, mock_which):
        mock_which.return_value = None
        executor = JobExecutor(logs_dir=self.logs_dir)
        with pytest.raises(ActNotFoundError, match="act is not installed"):
            executor.check_act()

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_check_docker_success(self, mock_which, mock_run):
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        mock_run.return_value = MagicMock(returncode=0)
        executor = JobExecutor(logs_dir=self.logs_dir)
        # Should not raise
        executor.check_docker()

    @patch("shutil.which")
    def test_check_docker_not_installed(self, mock_which):
        mock_which.side_effect = lambda name: "/usr/bin/act" if name == "act" else None
        executor = JobExecutor(logs_dir=self.logs_dir)
        with pytest.raises(DockerNotAvailableError, match="not installed"):
            executor.check_docker()

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_check_docker_not_running(self, mock_which, mock_run):
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        mock_run.return_value = MagicMock(returncode=1)
        executor = JobExecutor(logs_dir=self.logs_dir)
        with pytest.raises(DockerNotAvailableError, match="not running"):
            executor.check_docker()

    @patch("shutil.which")
    def test_run_dryrun(self, mock_which):
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        executor = JobExecutor(logs_dir=self.logs_dir)

        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            dryrun=True,
        )

        # Mock preflight checks
        with (
            patch.object(executor, "check_act", return_value="act 0.2.68"),
            patch.object(executor, "check_docker"),
        ):
            result = executor.run(
                cmd,
                matrix_name="GCC 15: C++20",
            )

        assert result.status == JobStatus.SKIPPED
        assert "DRY RUN" in result.stdout

    @patch("shutil.which")
    def test_run_preflight_act_fails(self, mock_which):
        mock_which.return_value = None
        executor = JobExecutor(logs_dir=self.logs_dir)

        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
        )
        result = executor.run(cmd, matrix_name="test")
        assert result.status == JobStatus.ERROR
        assert "not installed" in result.error_message

    @patch("shutil.which")
    def test_run_preflight_docker_fails(self, mock_which):
        mock_which.side_effect = lambda name: "/usr/bin/act" if name == "act" else None
        executor = JobExecutor(logs_dir=self.logs_dir)

        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
        )

        with patch.object(executor, "check_act", return_value="act 0.2.68"):
            result = executor.run(cmd, matrix_name="test")

        assert result.status == JobStatus.ERROR
        assert "not installed" in result.error_message

    @patch("shutil.which")
    def test_extract_error_finds_keywords(self, mock_which):
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=self.logs_dir)
        output = textwrap.dedent("""\
            Step 1: setup
            Step 2: build
            error: undefined reference to 'foo'
            fatal: compilation failed
            Step 3: cleanup
        """)
        extracted = executor._extract_error(output)
        assert "undefined reference" in extracted
        assert "compilation failed" in extracted

    @patch("shutil.which")
    def test_extract_error_fallback(self, mock_which):
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=self.logs_dir)
        output = "line 1\nline 2\nline 3"
        extracted = executor._extract_error(output, max_lines=2)
        assert "line 2" in extracted
        assert "line 3" in extracted

    @patch("shutil.which")
    def test_extract_error_http_401(self, mock_which):
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=self.logs_dir)
        # Auth line not in last two lines so max_lines=2 fallback cannot pass alone.
        output = (
            "setup: resolving action\n"
            "HTTP 401: unauthorized - Bad credentials\n"
            "middle: post-download\n"
            "all done\n"
            "finished ok"
        )
        extracted = executor._extract_error(output, max_lines=2)
        assert "401" in extracted
        assert "unauthorized" in extracted.lower()
        assert "finished ok" not in extracted

    @patch("shutil.which")
    def test_extract_error_http_403(self, mock_which):
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=self.logs_dir)
        output = (
            "setup: resolving action\n"
            "received HTTP status: 403 forbidden for this resource\n"
            "middle: post-download\n"
            "all done\n"
            "finished ok"
        )
        extracted = executor._extract_error(output, max_lines=2)
        assert "403" in extracted
        assert "forbidden" in extracted.lower()
        assert "finished ok" not in extracted

    @patch("shutil.which")
    def test_extract_error_ignores_401_false_positive(self, mock_which):
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=self.logs_dir)
        # 4010 line must not be in the last two lines so max_lines=2 fallback
        # differs from wrongly treating "401" inside "4010" as an error line.
        output = (
            "progress: fetched 4010 bytes from cache\n"
            "middle: still running\n"
            "all done\n"
            "finished ok"
        )
        extracted = executor._extract_error(output, max_lines=2)
        assert "4010" not in extracted
        assert "all done" in extracted
        assert "finished ok" in extracted

    def test_cleanup_temp_files(self, tmp_path):
        event = tmp_path / "event.json"
        event.write_text("{}")
        cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            event_file=event,
        )
        assert event.exists()
        JobExecutor._cleanup_temp_files(cmd)
        assert not event.exists()

    def test_cleanup_temp_files_none_cmd(self):
        # Should not raise
        JobExecutor._cleanup_temp_files(None)


# =====================================================================
# ActCommandBuilder tests
# =====================================================================


class TestActCommandBuilder:
    """Test command builder translation."""

    def test_basic_build(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(
            workflow_file=wf,
            project_dir=tmp_path,
        )
        entry = _make_entry()
        cmd = builder.build(entry)

        assert isinstance(cmd, ActCommand)
        assert cmd.workflow_file == wf
        assert cmd.job_id == "build"
        assert cmd.pull is False
        assert cmd.offline is False  # Default is online mode now
        assert cmd.privileged is True

    def test_env_vars_from_compiler(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry(cc="gcc-15", cxx="g++-15")
        cmd = builder.build(entry)

        assert cmd.env["CC"] == "gcc-15"
        assert cmd.env["CXX"] == "g++-15"

    def test_extra_env(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(entry, extra_env={"BOOST_ROOT": "/opt/boost"})

        assert cmd.env["BOOST_ROOT"] == "/opt/boost"

    def test_default_secrets(self, tmp_path):
        from localci.core.github_token import SENTINEL_GITHUB_TOKEN

        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(entry)

        assert cmd.secrets["GITHUB_TOKEN"] == SENTINEL_GITHUB_TOKEN

    def test_custom_default_secrets(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(
            workflow_file=wf,
            default_secrets={"MY_SECRET": "val"},
        )
        entry = _make_entry()
        cmd = builder.build(entry)

        assert cmd.secrets["MY_SECRET"] == "val"
        assert "GITHUB_TOKEN" in cmd.secrets

    def test_x86_architecture(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry(architecture="x86")
        cmd = builder.build(entry)

        assert cmd.container_architecture == "linux/386"

    def test_x86_64_no_arch_flag(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry(architecture="x86_64")
        cmd = builder.build(entry)

        assert cmd.container_architecture is None

    def test_dryrun_flag(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(entry, dryrun=True)

        assert cmd.dryrun is True

    def test_verbose_flag(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(entry, verbose=True)

        assert cmd.verbose is True

    def test_image_tag_mapping(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry(runs_on="ubuntu-latest")
        cmd = builder.build(entry, image_tag="my-image:latest")

        assert cmd.runner_mappings["ubuntu-latest"] == "my-image:latest"
        # Should also map the alias
        assert cmd.runner_mappings["ubuntu-24.04"] == "my-image:latest"

    def test_image_tag_mapping_24(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry(runs_on="ubuntu-24.04")
        cmd = builder.build(entry, image_tag="my-image:latest")

        assert cmd.runner_mappings["ubuntu-24.04"] == "my-image:latest"
        assert cmd.runner_mappings["ubuntu-latest"] == "my-image:latest"

    def test_no_image_tag_empty_mappings(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(entry, image_tag=None)

        assert cmd.runner_mappings == {}

    def test_matrix_filters_from_raw(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry(raw={"name": "GCC 15", "compiler": "gcc", "version": "15"})
        cmd = builder.build(entry)

        assert cmd.matrix_filters["compiler"] == "gcc"
        assert cmd.matrix_filters["version"] == "15"
        assert cmd.matrix_filters["name"] == "GCC 15"

    def test_event_file_created(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(entry)

        assert cmd.event_file is not None
        assert cmd.event_file.exists()
        content = json.loads(cmd.event_file.read_text())
        assert "push" in content

        # Cleanup
        cmd.event_file.unlink()

    def test_ccache_env_and_compress(self, tmp_path):
        """Issue 9: CCACHE_DIR, CCACHE_MAXSIZE, CCACHE_COMPRESS when cache enabled."""
        from localci.core.config import CacheConfig, CcacheConfig, ResolvedCachePaths

        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI")
        ccache_dir = tmp_path / "ccache"
        ccache_dir.mkdir()
        paths = ResolvedCachePaths(
            ccache_host=ccache_dir, boost_host=None, cmake_host=None
        )
        cfg = CacheConfig(
            ccache=CcacheConfig(enabled=True, max_size="2G", compress=True),
        )

        builder = ActCommandBuilder(workflow_file=wf)
        entry = _make_entry()
        cmd = builder.build(
            entry,
            resolved_cache_paths=paths,
            cache_config=cfg,
        )

        assert cmd.env["CCACHE_DIR"] == paths.ccache_container
        assert cmd.env["CCACHE_MAXSIZE"] == "2G"
        assert cmd.env["CCACHE_COMPRESS"] == "1"
        assert cmd.container_options is not None
        assert str(ccache_dir) in cmd.container_options


# =====================================================================
# DockerManager tests (mocked)
# =====================================================================


class TestDockerManager:
    """Test Docker operations with mocks."""

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_image_exists_true(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()
        mock_run.return_value = MagicMock(returncode=0)
        assert dm.image_exists("test:latest") is True

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_image_exists_false(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()
        mock_run.return_value = MagicMock(returncode=1)
        assert dm.image_exists("missing:latest") is False

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_load_image_missing_file(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()
        ok, msg = dm.load_image(Path("/nonexistent/image.tar"))
        assert ok is False
        assert "not found" in msg

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_load_image_success(self, mock_which, mock_run, tmp_path):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()

        tar_file = tmp_path / "test.tar"
        tar_file.write_text("fake tar")

        mock_run.return_value = MagicMock(
            returncode=0, stdout="Loaded image: test:latest"
        )
        ok, msg = dm.load_image(tar_file)
        assert ok is True
        assert "Loaded" in msg

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_image_size(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()

        # 100 MB
        mock_run.return_value = MagicMock(returncode=0, stdout=str(100 * 1024 * 1024))
        size = dm.image_size("test:latest")
        assert size is not None
        assert abs(size - 100.0) < 0.1

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_tag_image(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()

        mock_run.return_value = MagicMock(returncode=0)
        assert dm.tag_image("src:latest", "dst:latest") is True

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_remove_image(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()

        mock_run.return_value = MagicMock(returncode=0)
        assert dm.remove_image("test:latest") is True

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_cleanup_act_containers(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()

        mock_run.return_value = MagicMock(returncode=0, stdout="abc123\ndef456\n")
        count = dm.cleanup_act_containers()
        assert count == 2

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_cleanup_no_containers(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()

        mock_run.return_value = MagicMock(returncode=0, stdout="")
        count = dm.cleanup_act_containers()
        assert count == 0

    @patch("shutil.which")
    def test_docker_not_installed(self, mock_which):
        mock_which.return_value = None

        from localci.utils.docker import DockerManager

        with pytest.raises(DockerNotAvailableError, match="not installed"):
            DockerManager()

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_docker_not_responding(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        from localci.utils.docker import DockerManager

        with pytest.raises(DockerNotAvailableError, match="not responding"):
            DockerManager()

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_has_docker_property(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="docker 24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()
        assert dm.has_docker is True

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_docker_cmd_raises_when_path_cleared(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="24.0")

        from localci.utils.docker import DockerManager

        dm = DockerManager()
        dm._docker_path = None  # simulate -O / internal invariant break
        with pytest.raises(RuntimeError, match="Docker executable path not set") as exc_info:
            dm.build_cmd("version")
        assert type(exc_info.value) is RuntimeError
        assert not isinstance(exc_info.value, DockerNotAvailableError)


# =====================================================================
# ExecutionSummary tests
# =====================================================================


class TestExecutionSummary:
    """Test result aggregation."""

    def test_counts(self):
        summary = _make_summary(num_passed=3, num_failed=1)
        assert summary.total == 4
        assert summary.passed == 3
        assert summary.failed == 1
        assert summary.errors == 0
        assert summary.completed == 4

    def test_all_passed(self):
        summary = _make_summary(num_passed=3, num_failed=0)
        assert summary.all_passed is True

    def test_not_all_passed(self):
        summary = _make_summary(num_passed=2, num_failed=1)
        assert summary.all_passed is False

    def test_timing(self):
        summary = _make_summary()
        assert summary.total_duration > 0

    def test_longest_shortest_job(self):
        summary = _make_summary(num_passed=3, num_failed=0)
        longest = summary.longest_job
        shortest = summary.shortest_job
        assert longest is not None
        assert shortest is not None
        assert longest.duration_seconds >= shortest.duration_seconds

    def test_empty_summary(self):
        summary = ExecutionSummary(
            execution_id="empty",
            started_at=datetime.now(),
        )
        assert summary.total == 0
        assert summary.all_passed is True
        assert summary.longest_job is None
        assert summary.shortest_job is None

    def test_progress_line(self):
        summary = _make_summary(num_passed=2, num_failed=1)
        line = summary.progress_line()
        assert "3/3" in line

    def test_summary_report_all_passed(self):
        summary = _make_summary(num_passed=3, num_failed=0)
        report = summary.summary_report()
        assert "ALL PASSED" in report

    def test_summary_report_with_failures(self):
        summary = _make_summary(num_passed=2, num_failed=1)
        report = summary.summary_report()
        assert "FAILURES" in report
        assert "1 FAILED" in report

    def test_save_load_roundtrip(self, tmp_path):
        summary = _make_summary(num_passed=2, num_failed=1)
        out = tmp_path / "results.json"
        summary.save(out)

        assert out.exists()
        loaded = ExecutionSummary.load(out)

        assert loaded.execution_id == summary.execution_id
        assert loaded.total == summary.total
        assert loaded.passed == summary.passed
        assert loaded.failed == summary.failed
        assert len(loaded.results) == len(summary.results)

    def test_to_dict(self):
        summary = _make_summary(num_passed=1, num_failed=0)
        d = summary.to_dict()
        assert d["execution_id"] == "abc12345"
        assert d["total"] == 1
        assert d["passed"] == 1
        assert d["failed"] == 0
        assert len(d["results"]) == 1

    def test_load_with_missing_finished_at(self, tmp_path):
        data = {
            "execution_id": "test",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "results": [],
        }
        f = tmp_path / "test.json"
        f.write_text(json.dumps(data))

        loaded = ExecutionSummary.load(f)
        assert loaded.finished_at is None

    def test_error_count(self):
        summary = ExecutionSummary(
            execution_id="e",
            started_at=datetime.now(),
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=0,
                matrix_name="t",
                status=JobStatus.TIMEOUT,
            )
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=1,
                matrix_name="t2",
                status=JobStatus.ERROR,
            )
        )
        assert summary.errors == 2

    def test_pending_running_counts(self):
        summary = ExecutionSummary(
            execution_id="e",
            started_at=datetime.now(),
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=0,
                matrix_name="pending",
                status=JobStatus.PENDING,
            )
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=1,
                matrix_name="preparing",
                status=JobStatus.PREPARING,
            )
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=2,
                matrix_name="running",
                status=JobStatus.RUNNING,
            )
        )
        assert summary.pending == 2
        assert summary.running == 1

    def test_total_duration_no_finished_at(self):
        summary = ExecutionSummary(
            execution_id="e",
            started_at=datetime.now(),
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=0,
                matrix_name="t",
                status=JobStatus.PASSED,
                duration_seconds=10.0,
            )
        )
        summary.results.append(
            JobResult(
                job_id="build",
                matrix_index=1,
                matrix_name="t2",
                status=JobStatus.PASSED,
                duration_seconds=20.0,
            )
        )
        assert summary.total_duration == 30.0


# =====================================================================
# CLI integration tests
# =====================================================================


class TestCLIRunCommand:
    """Test the ``localci run`` CLI command."""

    def test_run_help(self):
        from click.testing import CliRunner

        from localci.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--job" in result.output
        assert "--platform" in result.output
        assert "--timeout" in result.output

    def test_run_dry_run(self):
        """Dry run should show the execution plan without running."""
        from click.testing import CliRunner

        from localci.cli.main import cli

        runner = CliRunner()
        fixtures = Path(__file__).parent / "fixtures"
        sample = fixtures / "sample_workflow.yml"

        if not sample.exists():
            pytest.skip("sample_workflow.yml not found")

        result = runner.invoke(cli, ["run", "--workflow", str(sample), "--dry-run"])
        # Should show dry-run output (may fail if no entries found)
        assert result.exit_code == 0 or "No matrix entries" in result.output


class TestCLIStatusCommand:
    """Test the ``localci status`` CLI command."""

    def test_status_help(self):
        from click.testing import CliRunner

        from localci.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--execution-id" in result.output
        assert "--format" in result.output

    def test_status_no_results(self):
        from click.testing import CliRunner

        from localci.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        # Should warn that no results exist
        assert "No previous execution" in result.output or result.exit_code == 0


class TestCLILogsCommand:
    """Test the ``localci logs`` CLI command."""

    def test_logs_help(self):
        from click.testing import CliRunner

        from localci.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["logs", "--help"])
        assert result.exit_code == 0
        assert "--tail" in result.output
        assert "--output" in result.output

    def test_logs_no_results(self):
        from click.testing import CliRunner

        from localci.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["logs", "0"])
        assert "No previous execution" in result.output or result.exit_code == 0
