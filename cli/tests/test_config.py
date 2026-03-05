"""Tests for the configuration module.

Validates Pydantic models, config loading, file discovery, and YAML
round-tripping.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from localci.core.config import (
    CacheConfig,
    LocalCIConfig,
    default_config_yaml,
    find_config_file,
    load_config,
    resolve_cache_paths,
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
        # Validator runs on default (validate_default=True), so path is expanded/absolute
        assert cfg.workflow.is_absolute()
        assert cfg.workflow.name == "ci.yml"
        assert ".github" in cfg.workflow.parts and "workflows" in cfg.workflow.parts


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


# ---------------------------------------------------------------------------
# Phase 2 cache path resolution
# ---------------------------------------------------------------------------


class TestResolveCachePaths:
    """resolve_cache_paths returns correct host paths when cache enabled."""

    def test_resolve_cache_paths_default(self):
        cfg = LocalCIConfig()
        r = resolve_cache_paths(cfg.cache, False, None, "build", "build:gcc-15")
        assert r is not None
        assert r.ccache_host is not None
        assert r.boost_host is not None
        assert r.cmake_host is not None
        assert "ccache" in str(r.ccache_host)
        assert "boost" in str(r.boost_host)
        assert "cmake" in str(r.cmake_host)
        assert "build" in str(r.cmake_host)
        assert r.b2_source_host is not None
        assert "b2-source" in str(r.b2_source_host)
        assert len(r.host_dirs_to_ensure()) == 4

    def test_resolve_cache_paths_no_cache(self):
        cfg = LocalCIConfig()
        assert resolve_cache_paths(cfg.cache, True, None, "build", "build:gcc-15") is None

    def test_resolve_cache_paths_cache_disabled(self):
        cache = CacheConfig(enabled=False)
        assert resolve_cache_paths(cache, False, None, "build", "build:gcc-15") is None

    def test_resolve_cache_paths_override_dir(self, tmp_path):
        cfg = LocalCIConfig()
        r = resolve_cache_paths(cfg.cache, False, tmp_path, "build", "build:gcc-15")
        assert r is not None
        assert str(r.ccache_host).startswith(str(tmp_path))

    def test_resolve_cache_paths_cmake_input_digest(self):
        """Issue 11: CMake path includes input digest when provided."""
        cfg = LocalCIConfig()
        r = resolve_cache_paths(
            cfg.cache,
            False,
            None,
            "build",
            "build:gcc-15",
            cmake_input_digest="a1b2c3d4e5f6",
        )
        assert r is not None
        assert r.cmake_host is not None
        assert "a1b2c3d4e5f6" in str(r.cmake_host)
        assert r.cmake_host.name == "build-gcc-15_a1b2c3d4e5f6"

    def test_resolve_cache_paths_b2_source_when_boost_enabled(self):
        """B2 source cache path is set per job when boost cache and build_dir enabled."""
        cfg = LocalCIConfig()
        r = resolve_cache_paths(
            cfg.cache, False, None, "build", "build:gcc-15"
        )
        assert r is not None
        assert r.b2_source_host is not None
        assert "b2-source" in str(r.b2_source_host)
        assert "build-gcc-15" in str(r.b2_source_host)

    def test_resolve_cache_paths_b2_source_disabled_when_build_dir_false(self):
        """B2 source cache path is None when boost.build_dir is False."""
        cfg = LocalCIConfig()
        cfg = cfg.model_copy(
            update={
                "cache": cfg.cache.model_copy(
                    update={
                        "boost": cfg.cache.boost.model_copy(
                            update={"build_dir": False}
                        )
                    }
                )
            }
        )
        r = resolve_cache_paths(
            cfg.cache, False, None, "build", "build:gcc-15"
        )
        assert r is not None
        assert r.b2_source_host is None

    def test_ccache_compress_default(self):
        """Issue 9: CcacheConfig.compress defaults to True."""
        cfg = LocalCIConfig()
        assert cfg.cache.ccache.compress is True
