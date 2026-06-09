"""Tests for the CLI commands.

Validates that Click correctly parses all commands, options, and arguments.
Business logic is stubbed, so these tests focus on the CLI *surface*.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
from localci.errors import YqNotFoundError
from localci.utils.crash import CRASH_LOG_NAME

runner = CliRunner()

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CI = str(FIXTURES_DIR / "sample_ci.yml")
SAMPLE_WORKFLOW = str(FIXTURES_DIR / "sample_workflow.yml")


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


class TestRootGroup:
    """Root ``localci`` command group."""

    def test_help(self):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Local CI" in result.output

    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_all_commands_listed_in_help(self):
        result = runner.invoke(cli, ["--help"])
        for cmd in ("analyze", "list", "run", "status", "logs", "images", "config"):
            assert cmd in result.output, f"'{cmd}' missing from --help output"


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


class TestAnalyze:
    """``localci analyze`` command."""

    def test_help(self):
        result = runner.invoke(cli, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "WORKFLOW" in result.output

    def test_missing_workflow_argument(self):
        result = runner.invoke(cli, ["analyze"])
        assert result.exit_code != 0

    def test_basic_invocation(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI\n")
        result = runner.invoke(cli, ["analyze", str(wf)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    """``localci list`` command."""

    def test_help(self):
        result = runner.invoke(cli, ["list", "--help"])
        assert result.exit_code == 0
        assert "--platform" in result.output

    def test_basic_invocation(self):
        result = runner.invoke(cli, ["list", "--workflow", SAMPLE_CI])
        assert result.exit_code == 0

    def test_platform_filter(self):
        result = runner.invoke(
            cli, ["list", "--workflow", SAMPLE_CI, "--platform", "linux"]
        )
        assert result.exit_code == 0

    def test_no_workflow_errors(self, tmp_path, monkeypatch):
        """Without --workflow, list fails when no workflow file is reachable."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["list"])
        assert result.exit_code != 0

    def test_list_enabled_with_config(self, tmp_path):
        """--enabled filters by config.jobs.include when present."""
        config_file = tmp_path / ".localci.yml"
        config_file.write_text(
            f"version: 1\nworkflow: {SAMPLE_CI}\njobs:\n  include:\n    - GCC 15\n"
        )
        result = runner.invoke(
            cli,
            [
                "-c",
                str(config_file),
                "list",
                "--enabled",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        entries = json.loads(result.output)
        assert entries
        assert all("GCC 15" in e["name"] for e in entries)

    def test_list_disabled_with_config(self, tmp_path):
        """--disabled filters by config.jobs.exclude when present."""
        config_file = tmp_path / ".localci.yml"
        config_file.write_text(
            f"version: 1\nworkflow: {SAMPLE_CI}\njobs:\n  exclude:\n    - GCC 15\n"
        )
        result = runner.invoke(
            cli,
            [
                "-c",
                str(config_file),
                "list",
                "--disabled",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        entries = json.loads(result.output)
        assert entries
        assert all("GCC 15" in e["name"] for e in entries)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    """``localci run`` command."""

    def test_help(self):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_dry_run(self):
        result = runner.invoke(cli, ["run", "--workflow", SAMPLE_WORKFLOW, "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.output or "dry" in result.output.lower()

    def test_dry_run_with_job(self):
        result = runner.invoke(
            cli,
            ["run", "--workflow", SAMPLE_WORKFLOW, "--dry-run", "--job", "GCC"],
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestStatus:
    """``localci status`` command."""

    def test_help(self):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--follow" in result.output
        assert "--format" in result.output

    def test_basic_invocation(self):
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0

    def test_status_uses_last_status_json_and_json_format(self, tmp_path):
        """When last-status.json exists, status uses MCP schema; --format json outputs it."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "last-status.json").write_text(
            '{"progress": "2/3 jobs completed", "execution_id": "abc", "total": 3}',
            encoding="utf-8",
        )
        config_yml = tmp_path / ".localci.yml"
        config_yml.write_text(
            f"version: 1\nlogging:\n  directory: {logs_dir!s}\n",
            encoding="utf-8",
        )
        result = runner.invoke(
            cli,
            ["--config", str(config_yml), "status", "--format", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("progress") == "2/3 jobs completed"
        assert data.get("execution_id") == "abc"
        assert data.get("total") == 3


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


class TestLogs:
    """``localci logs`` command."""

    def test_help(self):
        result = runner.invoke(cli, ["logs", "--help"])
        assert result.exit_code == 0
        assert "--tail" in result.output

    def test_basic_invocation(self):
        result = runner.invoke(cli, ["logs", "5"])
        assert result.exit_code == 0

    def test_missing_job_argument(self):
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


class TestImages:
    """``localci images`` command group."""

    def test_help(self):
        result = runner.invoke(cli, ["images", "--help"])
        assert result.exit_code == 0
        for sub in ("list", "info", "build", "clean", "import", "export"):
            assert sub in result.output, f"'{sub}' missing from images --help"

    def test_images_list(self):
        result = runner.invoke(cli, ["images", "list"])
        assert result.exit_code == 0

    def test_images_info(self):
        result = runner.invoke(cli, ["images", "info", "capy-ubuntu-24.04-base"])
        assert result.exit_code == 0

    def test_images_build_all(self):
        # Avoid invoking Docker in CLI surface tests.
        result = runner.invoke(cli, ["images", "build"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfig:
    """``localci config`` command group."""

    def test_help(self):
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        for sub in ("show", "init", "set", "get"):
            assert sub in result.output, f"'{sub}' missing from config --help"

    def test_config_show(self):
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0

    def test_config_init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["config", "init"])
        assert result.exit_code == 0
        assert (tmp_path / ".localci.yml").exists()

    def test_config_init_no_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".localci.yml").write_text("version: 1\n")
        result = runner.invoke(cli, ["config", "init"])
        # Should fail because file already exists.
        assert result.exit_code != 0 or "already exists" in result.output

    def test_config_init_force(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".localci.yml").write_text("version: 1\n")
        result = runner.invoke(cli, ["config", "init", "--force"])
        assert result.exit_code == 0

    def test_config_get(self):
        result = runner.invoke(cli, ["config", "get", "parallel.max_jobs"])
        assert result.exit_code == 0
        assert "8" in result.output

    def test_config_get_missing_key(self):
        result = runner.invoke(cli, ["config", "get", "nonexistent.key"])
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_config_set_and_get(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Init a config first.
        runner.invoke(cli, ["config", "init"])
        # Set a value.
        result = runner.invoke(
            cli,
            [
                "-c",
                str(tmp_path / ".localci.yml"),
                "config",
                "set",
                "parallel.max_jobs",
                "16",
            ],
        )
        assert result.exit_code == 0
        # Verify.
        result = runner.invoke(
            cli,
            [
                "-c",
                str(tmp_path / ".localci.yml"),
                "config",
                "get",
                "parallel.max_jobs",
            ],
        )
        assert result.exit_code == 0
        assert "16" in result.output


# ---------------------------------------------------------------------------
# Catch-all exception handler
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_localci_home(monkeypatch, tmp_path):
    """Redirect ``localci_home()`` to a temp dir (Unix and Windows)."""
    home = tmp_path / ".localci"
    home.mkdir(parents=True, exist_ok=True)

    def _home():
        return home

    monkeypatch.setattr("localci.utils.paths.localci_home", _home)
    monkeypatch.setattr("localci.utils.crash.localci_home", _home)
    return home


class TestCatchAllHandler:
    """Unhandled exceptions produce friendly output and crash.log diagnostics."""

    @staticmethod
    def _workflow_file(tmp_path: Path) -> Path:
        wf = tmp_path / "ci.yml"
        wf.write_text("name: CI\n")
        return wf

    @patch("localci.cli.analyze.WorkflowAnalyzer.analyze")
    def test_unhandled_exception_friendly_output(
        self, mock_analyze, tmp_path, isolated_localci_home
    ):
        mock_analyze.side_effect = RuntimeError("test boom")
        wf = self._workflow_file(tmp_path)
        result = runner.invoke(cli, ["analyze", str(wf)])
        assert result.exit_code == 2
        assert "Traceback" not in result.output
        assert "unexpected internal error" in result.output.lower()
        assert "RuntimeError" in result.output
        assert "test boom" in result.output
        assert "bug report" in result.output.lower()
        assert f"~/.localci/{CRASH_LOG_NAME}" in result.output.replace("\n", " ")

    @patch("localci.cli.analyze.WorkflowAnalyzer.analyze")
    def test_unhandled_exception_writes_crash_log(
        self, mock_analyze, tmp_path, isolated_localci_home
    ):
        mock_analyze.side_effect = RuntimeError("test boom")
        wf = self._workflow_file(tmp_path)
        runner.invoke(cli, ["analyze", str(wf)])
        log_path = isolated_localci_home / CRASH_LOG_NAME
        assert log_path.is_file()
        content = log_path.read_text(encoding="utf-8")
        assert "RuntimeError" in content
        assert "test boom" in content
        assert "traceback:" in content.lower()
        assert "localci_version:" in content
        assert "python_version:" in content
        assert "platform:" in content
        assert content.split("python_version:", 1)[1].split("\n", 1)[0].strip()
        assert content.split("platform:", 1)[1].split("\n", 1)[0].strip()

    @patch("localci.cli.analyze.WorkflowAnalyzer.analyze")
    def test_debug_flag_reraises_exception(
        self, mock_analyze, tmp_path, isolated_localci_home
    ):
        mock_analyze.side_effect = RuntimeError("test boom")
        wf = self._workflow_file(tmp_path)
        with pytest.raises(RuntimeError, match="test boom"):
            runner.invoke(
                cli,
                ["--debug", "analyze", str(wf)],
                catch_exceptions=False,
            )

    @patch("localci.cli.analyze.WorkflowAnalyzer.analyze")
    def test_leaked_localci_error_exit_one_no_crash_log(
        self, mock_analyze, tmp_path, isolated_localci_home
    ):
        mock_analyze.side_effect = YqNotFoundError()
        wf = self._workflow_file(tmp_path)
        result = runner.invoke(cli, ["analyze", str(wf)])
        assert result.exit_code == 1
        assert "unexpected internal error" not in result.output.lower()
        assert "mikefarah/yq" in result.output.lower()
        assert not (isolated_localci_home / CRASH_LOG_NAME).exists()

    @patch("localci.core.executor.JobExecutor.check_act")
    def test_run_unhandled_exception_uses_catch_all(
        self, mock_check_act, isolated_localci_home, tmp_path
    ):
        mock_check_act.side_effect = RuntimeError("act subprocess failed")
        if not Path(SAMPLE_WORKFLOW).exists():
            pytest.skip("sample_workflow.yml not found")
        logs_dir = tmp_path / "logs"
        cache_dir = tmp_path / "cache"
        images_registry = tmp_path / "images"
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text(
            "logging:\n"
            f"  directory: {logs_dir.as_posix()}\n"
            "cache:\n"
            f"  directory: {cache_dir.as_posix()}\n"
            "  boost:\n"
            "    enabled: false\n"
            "images:\n"
            f"  registry: {images_registry.as_posix()}\n"
        )
        result = runner.invoke(
            cli,
            ["-c", str(cfg_file), "run", "--workflow", SAMPLE_WORKFLOW],
        )
        assert result.exit_code == 2
        assert "unexpected internal error" in result.output.lower()
        assert "act subprocess failed" in result.output
        mock_check_act.assert_called_once()
        assert (isolated_localci_home / CRASH_LOG_NAME).is_file()

    def test_config_error_exit_code_unchanged(self, tmp_path):
        result = runner.invoke(
            cli,
            ["--config", str(tmp_path / "missing.yml"), "list"],
        )
        assert result.exit_code == 1

    @patch("localci.utils.crash.crash_log_path")
    @patch("localci.cli.analyze.WorkflowAnalyzer.analyze")
    def test_log_crash_write_failure_still_friendly(
        self, mock_analyze, mock_crash_log_path, tmp_path, isolated_localci_home
    ):
        mock_analyze.side_effect = RuntimeError("test boom")
        mock_path = MagicMock()
        mock_path.write_text.side_effect = OSError("disk full")
        mock_crash_log_path.return_value = mock_path
        wf = self._workflow_file(tmp_path)
        result = runner.invoke(cli, ["analyze", str(wf)])
        assert result.exit_code == 2
        assert "unexpected internal error" in result.output.lower()
        assert "stderr" in result.output.lower()
        assert not (isolated_localci_home / CRASH_LOG_NAME).exists()
