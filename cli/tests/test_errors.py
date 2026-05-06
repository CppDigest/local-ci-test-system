"""Tests for structured error types (Issue #28).

Covers:
- Exception hierarchy under :class:`~localci.errors.LocalCIError` (config, workflow,
  execution, yq families) and attribute contracts for config/workflow parse types
- Backward-compatibility: structured errors are still caught by built-in base classes
- load_config raises structured errors for each failure mode
- CLI exits with code 1 and an actionable message for config error paths
- WorkflowAnalyzer raises WorkflowNotFoundError / WorkflowParseError
- CLI run/list exit cleanly on workflow errors
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from localci.cli.main import cli
from localci.core.config import load_config
from localci.core.workflow import WorkflowAnalyzer
from localci.errors import (
    ActNotFoundError,
    ConfigError,
    ConfigFileNotFoundError,
    ConfigIOError,
    ConfigValidationError,
    CyclicDependencyError,
    DockerNotAvailableError,
    ExecutionError,
    LocalCIError,
    MissingFieldError,
    UnsupportedMatrixError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowParseError,
    YqError,
    YqNotFoundError,
)


runner = CliRunner()


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Structured errors used by the CLI and core are under LocalCIError."""

    def test_config_error_is_local_ci_error(self):
        assert issubclass(ConfigError, LocalCIError)

    def test_config_file_not_found_is_config_error(self):
        assert issubclass(ConfigFileNotFoundError, ConfigError)

    def test_config_file_not_found_is_file_not_found_error(self):
        """Backward-compat: caught by except FileNotFoundError."""
        assert issubclass(ConfigFileNotFoundError, FileNotFoundError)

    def test_config_io_error_is_config_error(self):
        assert issubclass(ConfigIOError, ConfigError)

    def test_config_io_error_is_os_error(self):
        """Backward-compat: caught by except OSError."""
        assert issubclass(ConfigIOError, OSError)

    def test_config_validation_error_is_config_error(self):
        assert issubclass(ConfigValidationError, ConfigError)

    def test_workflow_error_is_local_ci_error(self):
        assert issubclass(WorkflowError, LocalCIError)

    def test_workflow_not_found_is_workflow_error(self):
        assert issubclass(WorkflowNotFoundError, WorkflowError)

    def test_workflow_not_found_is_file_not_found_error(self):
        """Backward-compat: caught by except FileNotFoundError."""
        assert issubclass(WorkflowNotFoundError, FileNotFoundError)

    def test_workflow_parse_error_is_workflow_error(self):
        assert issubclass(WorkflowParseError, WorkflowError)

    def test_missing_field_and_matrix_errors_are_workflow_error(self):
        assert issubclass(MissingFieldError, WorkflowError)
        assert issubclass(UnsupportedMatrixError, WorkflowError)

    def test_cyclic_dependency_is_workflow_error(self):
        assert issubclass(CyclicDependencyError, WorkflowError)

    def test_execution_family_under_local_ci_error(self):
        assert issubclass(ExecutionError, LocalCIError)
        assert issubclass(ActNotFoundError, ExecutionError)
        assert issubclass(DockerNotAvailableError, ExecutionError)

    def test_yq_errors_under_local_ci_error(self):
        assert issubclass(YqError, LocalCIError)
        assert issubclass(YqNotFoundError, LocalCIError)


# ---------------------------------------------------------------------------
# Error attribute contracts
# ---------------------------------------------------------------------------


class TestErrorAttributes:
    """Each error type carries expected metadata attributes."""

    def test_config_file_not_found_attributes(self, tmp_path):
        p = tmp_path / "missing.yml"
        exc = ConfigFileNotFoundError(p)
        assert exc.path == p
        assert exc.cause is None
        assert str(p) in str(exc)

    def test_config_file_not_found_with_cause(self, tmp_path):
        p = tmp_path / "missing.yml"
        original = FileNotFoundError("original")
        exc = ConfigFileNotFoundError(p, cause=original)
        assert exc.cause is original

    def test_config_io_error_attributes(self, tmp_path):
        p = tmp_path / "locked.yml"
        original = PermissionError("permission denied")
        exc = ConfigIOError(p, original)
        assert exc.path == p
        assert exc.cause is original
        assert str(p) in str(exc)

    def test_config_validation_error_attributes_with_path(self, tmp_path):
        p = tmp_path / ".localci.yml"
        original = ValueError("bad value")
        exc = ConfigValidationError(p, original)
        assert exc.path == p
        assert exc.cause is original
        assert str(p) in str(exc)

    def test_config_validation_error_attributes_no_path(self):
        original = ValueError("bad value")
        exc = ConfigValidationError(None, original)
        assert exc.path is None
        assert exc.cause is original

    def test_workflow_not_found_attributes(self, tmp_path):
        p = tmp_path / "ci.yml"
        exc = WorkflowNotFoundError(p)
        assert exc.path == p
        assert exc.cause is None
        assert str(p) in str(exc)

    def test_workflow_not_found_with_cause(self, tmp_path):
        p = tmp_path / "ci.yml"
        original = FileNotFoundError("no file")
        exc = WorkflowNotFoundError(p, cause=original)
        assert exc.cause is original

    def test_workflow_parse_error_attributes(self, tmp_path):
        p = tmp_path / "ci.yml"
        original = ValueError("unexpected key")
        exc = WorkflowParseError(p, original)
        assert exc.path == p
        assert exc.cause is original
        assert "unexpected key" in str(exc)
        assert str(p) in str(exc)


# ---------------------------------------------------------------------------
# Backward-compatibility: caught by built-in types
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Structured errors are still intercepted by legacy except clauses."""

    def test_config_file_not_found_caught_as_file_not_found(self, tmp_path):
        p = tmp_path / "missing.yml"
        with pytest.raises(FileNotFoundError):
            raise ConfigFileNotFoundError(p)

    def test_config_io_error_caught_as_os_error(self, tmp_path):
        p = tmp_path / "locked.yml"
        with pytest.raises(OSError):
            raise ConfigIOError(p, PermissionError("denied"))

    def test_workflow_not_found_caught_as_file_not_found(self, tmp_path):
        p = tmp_path / "ci.yml"
        with pytest.raises(FileNotFoundError):
            raise WorkflowNotFoundError(p)


# ---------------------------------------------------------------------------
# load_config raises structured errors
# ---------------------------------------------------------------------------


class TestLoadConfigStructuredErrors:
    """load_config raises the correct structured exception for each failure mode."""

    def test_file_not_found_raises_config_file_not_found(self):
        with pytest.raises(ConfigFileNotFoundError) as exc_info:
            load_config("/nonexistent/path/.localci.yml")
        assert ".localci.yml" in str(exc_info.value)

    def test_file_not_found_still_caught_as_file_not_found_error(self):
        """Backward-compat with existing except FileNotFoundError handlers."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/.localci.yml")

    def test_file_not_found_carries_path_attribute(self):
        with pytest.raises(ConfigFileNotFoundError) as exc_info:
            load_config("/nonexistent/path/.localci.yml")
        assert exc_info.value.path == Path("/nonexistent/path/.localci.yml")

    def test_validation_error_raises_config_validation_error(self, tmp_path):
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("parallel:\n  max_jobs: 999\n")  # exceeds max of 64
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(cfg_file)
        assert exc_info.value.path == cfg_file
        assert exc_info.value.cause is not None

    def test_validation_error_carries_cause(self, tmp_path):
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("parallel:\n  max_jobs: 0\n")  # below min of 1
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(cfg_file)
        from pydantic import ValidationError
        assert isinstance(exc_info.value.cause, ValidationError)

    def test_valid_config_still_loads(self, tmp_path):
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("version: 1\nevent: push\n")
        cfg = load_config(cfg_file)
        assert cfg.version == 1

    def test_io_error_raises_config_io_error(self, tmp_path, monkeypatch):
        """ConfigIOError is raised when the file cannot be read."""
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("version: 1\n")

        import builtins
        original_open = builtins.open

        def broken_open(path, *args, **kwargs):
            if Path(str(path)) == cfg_file:
                raise PermissionError("permission denied")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", broken_open)

        with pytest.raises(ConfigIOError) as exc_info:
            load_config(cfg_file)
        assert exc_info.value.path == cfg_file
        assert isinstance(exc_info.value.cause, PermissionError)

    def test_malformed_yaml_raises_config_io_error(self, tmp_path):
        """Invalid YAML is surfaced as ConfigIOError (same as read/parse failures)."""
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("version: 1\n  bad_indent: x\n")

        with pytest.raises(ConfigIOError) as exc_info:
            load_config(cfg_file)
        assert exc_info.value.path == cfg_file
        assert isinstance(exc_info.value.cause, yaml.YAMLError)


# ---------------------------------------------------------------------------
# WorkflowAnalyzer raises structured errors
# ---------------------------------------------------------------------------


class TestWorkflowAnalyzerStructuredErrors:
    """WorkflowAnalyzer.analyze raises WorkflowNotFoundError / WorkflowParseError."""

    def test_missing_workflow_raises_workflow_not_found(self, tmp_path):
        p = tmp_path / "nonexistent.yml"
        analyzer = WorkflowAnalyzer()
        with pytest.raises(WorkflowNotFoundError) as exc_info:
            analyzer.analyze(p)
        assert exc_info.value.path == p

    def test_missing_workflow_caught_as_file_not_found(self, tmp_path):
        """Backward-compat."""
        p = tmp_path / "nonexistent.yml"
        analyzer = WorkflowAnalyzer()
        with pytest.raises(FileNotFoundError):
            analyzer.analyze(p)

    def test_missing_workflow_is_workflow_error(self, tmp_path):
        p = tmp_path / "nonexistent.yml"
        analyzer = WorkflowAnalyzer()
        with pytest.raises(WorkflowError):
            analyzer.analyze(p)


# ---------------------------------------------------------------------------
# CLI: config error paths surface actionable messages and exit 1
# ---------------------------------------------------------------------------


class TestCliConfigErrorPaths:
    """CLI exits with code 1 and prints an actionable message for config errors."""

    def test_explicit_config_not_found(self, tmp_path):
        result = runner.invoke(cli, ["--config", str(tmp_path / "missing.yml"), "list"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "missing" in result.output.lower()

    def test_invalid_config_surfaces_validation_detail(self, tmp_path):
        cfg = tmp_path / ".localci.yml"
        cfg.write_text("parallel:\n  max_jobs: 999\n")
        result = runner.invoke(cli, ["--config", str(cfg), "list"])
        assert result.exit_code != 0
        # Should include something actionable — not just "Failed to load config"
        assert "invalid config" in result.output.lower() or "999" in result.output or "max_jobs" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: workflow error paths surface actionable messages and exit 1
# ---------------------------------------------------------------------------


_FIXTURES = Path(__file__).parent / "fixtures"


class TestCliWorkflowErrorPaths:
    """CLI run/list exits cleanly when the workflow file is missing or unparseable."""

    def test_run_missing_workflow(self, tmp_path):
        result = runner.invoke(
            cli, ["run", "--workflow", str(tmp_path / "nonexistent.yml"), "--dry-run"]
        )
        assert result.exit_code != 0

    def test_list_missing_workflow(self, tmp_path):
        result = runner.invoke(
            cli, ["list", "--workflow", str(tmp_path / "nonexistent.yml")]
        )
        assert result.exit_code != 0
