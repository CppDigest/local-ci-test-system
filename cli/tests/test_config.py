"""Tests for the configuration module.

Validates Pydantic models, config loading, file discovery, and YAML
round-tripping.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from localci.core.config import (
    LocalCIConfig,
    default_config_yaml,
    find_config_file,
    load_config,
)


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    """Default configuration values."""

    def test_default_values(self):
        cfg = LocalCIConfig()
        assert cfg.version == 1
        assert cfg.event == "push"
        assert cfg.parallel.max_jobs == 8
        assert cfg.platforms.linux is True
        assert cfg.platforms.windows is False
        assert cfg.platforms.macos is False
        assert cfg.execution.timeout == 3600
        assert cfg.execution.keep_containers is False

    def test_workflow_default(self):
        cfg = LocalCIConfig()
        assert cfg.workflow == Path(".github/workflows/ci.yml")


# ---------------------------------------------------------------------------
# Config loading from YAML
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Loading config from YAML files."""

    def test_load_minimal(self, tmp_path):
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("version: 1\nevent: pull_request\n")
        cfg = load_config(cfg_file)
        assert cfg.version == 1
        assert cfg.event == "pull_request"
        # Other fields should be defaults.
        assert cfg.parallel.max_jobs == 8

    def test_load_full(self, tmp_path):
        data = {
            "version": 1,
            "workflow": ".github/workflows/test.yml",
            "event": "push",
            "parallel": {"max_jobs": 16, "resource_limit": {"cpu_percent": 90, "memory_percent": 80}},
            "platforms": {"linux": True, "windows": True, "macos": False},
            "jobs": {"include": ["build"], "exclude": ["changelog"]},
            "priorities": {"GCC 15": 1, "Clang 20": 2},
            "execution": {"timeout": 7200, "keep_containers": True},
        }
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text(yaml.dump(data))
        cfg = load_config(cfg_file)
        assert cfg.parallel.max_jobs == 16
        assert cfg.parallel.resource_limit.cpu_percent == 90
        assert cfg.platforms.windows is True
        assert cfg.jobs.include == ["build"]
        assert cfg.jobs.exclude == ["changelog"]
        assert cfg.priorities["GCC 15"] == 1
        assert cfg.execution.timeout == 7200
        assert cfg.execution.keep_containers is True

    def test_logging_directory_expands_tilde(self, tmp_path):
        """logging.directory with ~ is expanded so run/status/logs use same path."""
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("logging:\n  directory: ~/.localci/logs\n")
        cfg = load_config(cfg_file)
        assert "~" not in str(cfg.logging.directory)
        assert cfg.logging.directory.is_absolute()

    def test_logging_directory_expands_tilde_direct(self):
        """LoggingConfig expands ~ on direct construction (field_validator runs)."""
        from localci.core.config import LoggingConfig

        cfg = LoggingConfig(directory="~/.localci/logs")
        assert "~" not in str(cfg.directory)
        assert cfg.directory.is_absolute()

    def test_load_empty_file(self, tmp_path):
        cfg_file = tmp_path / ".localci.yml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        # Should return defaults without error.
        assert cfg.version == 1

    def test_load_missing_file(self):
        try:
            load_config("/nonexistent/path/.localci.yml")
            assert False, "Expected FileNotFoundError"
        except FileNotFoundError:
            pass

    def test_load_no_path_returns_defaults(self, tmp_path, monkeypatch):
        # Change to a directory with no config file.
        monkeypatch.chdir(tmp_path)
        cfg = load_config(None)
        assert cfg.version == 1


# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------


class TestConfigDiscovery:
    """Finding .localci.yml by walking up the directory tree."""

    def test_find_in_current_dir(self, tmp_path):
        (tmp_path / ".localci.yml").write_text("version: 1\n")
        found = find_config_file(tmp_path)
        assert found is not None
        assert found.name == ".localci.yml"

    def test_find_in_parent_dir(self, tmp_path):
        (tmp_path / ".localci.yml").write_text("version: 1\n")
        child = tmp_path / "subdir"
        child.mkdir()
        found = find_config_file(child)
        assert found is not None

    def test_not_found(self, tmp_path):
        child = tmp_path / "empty_project"
        child.mkdir()
        found = find_config_file(child)
        assert found is None

    def test_prefers_yml_over_yaml(self, tmp_path):
        (tmp_path / ".localci.yml").write_text("version: 1\n")
        (tmp_path / ".localci.yaml").write_text("version: 2\n")
        found = find_config_file(tmp_path)
        assert found is not None
        assert found.name == ".localci.yml"


# ---------------------------------------------------------------------------
# Default config YAML generation
# ---------------------------------------------------------------------------


class TestDefaultConfigYaml:
    """Generating a default .localci.yml file."""

    def test_generates_valid_yaml(self):
        text = default_config_yaml()
        data = yaml.safe_load(text)
        assert isinstance(data, dict)
        assert data["version"] == 1
        assert data["event"] == "push"

    def test_round_trip(self):
        """Default YAML can be loaded back into a valid config."""
        text = default_config_yaml()
        data = yaml.safe_load(text)
        cfg = LocalCIConfig.model_validate(data)
        assert cfg.version == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Pydantic validation of config values."""

    def test_max_jobs_bounds(self):
        cfg = LocalCIConfig(parallel={"max_jobs": 1})
        assert cfg.parallel.max_jobs == 1

    def test_max_jobs_too_low(self):
        try:
            LocalCIConfig(parallel={"max_jobs": 0})
            assert False, "Expected validation error"
        except Exception:
            pass

    def test_max_jobs_too_high(self):
        try:
            LocalCIConfig(parallel={"max_jobs": 100})
            assert False, "Expected validation error"
        except Exception:
            pass
