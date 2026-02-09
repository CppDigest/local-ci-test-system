"""Unit tests for the YqWrapper (utils/yq.py).

All tests use the PyYAML fallback so they work without ``yq`` installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from localci.utils.yq import YqNotFoundError, YqWrapper

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CI = FIXTURES_DIR / "sample_ci.yml"


@pytest.fixture
def yq():
    wrapper = YqWrapper()
    wrapper.clear_cache()
    return wrapper


# =====================================================================
# Basic loading
# =====================================================================


class TestYqBasicLoading:
    def test_workflow_name(self, yq):
        assert yq.workflow_name(SAMPLE_CI) == "CI Test"

    def test_events(self, yq):
        events = yq.events(SAMPLE_CI)
        assert "push" in events
        assert "pull_request" in events

    def test_event_branches(self, yq):
        branches = yq.event_branches(SAMPLE_CI, "push")
        assert "develop" in branches

    def test_global_env(self, yq):
        env = yq.global_env(SAMPLE_CI)
        assert env["PYTHON_VERSION"] == "3.11"

    def test_concurrency(self, yq):
        # sample_ci.yml does not define concurrency
        assert yq.concurrency(SAMPLE_CI) is None


# =====================================================================
# Job queries
# =====================================================================


class TestYqJobQueries:
    def test_job_names(self, yq):
        names = yq.job_names(SAMPLE_CI)
        assert "build" in names
        assert "changelog" in names

    def test_job_data(self, yq):
        data = yq.job_data(SAMPLE_CI, "build")
        assert "strategy" in data
        assert "steps" in data

    def test_job_needs(self, yq):
        needs = yq.job_needs(SAMPLE_CI, "changelog")
        assert "build" in needs

    def test_job_needs_empty(self, yq):
        needs = yq.job_needs(SAMPLE_CI, "build")
        assert needs == []

    def test_job_runs_on(self, yq):
        runs_on = yq.job_runs_on(SAMPLE_CI, "changelog")
        assert "ubuntu" in runs_on

    def test_job_timeout(self, yq):
        assert yq.job_timeout(SAMPLE_CI, "build") == 120

    def test_job_defaults(self, yq):
        defaults = yq.job_defaults(SAMPLE_CI, "build")
        assert defaults is not None
        assert "run" in defaults

    def test_job_env(self, yq):
        env = yq.job_env(SAMPLE_CI, "build")
        # build job has no direct env in the fixture
        assert isinstance(env, dict)


# =====================================================================
# Matrix queries
# =====================================================================


class TestYqMatrixQueries:
    def test_matrix_strategy(self, yq):
        strategy = yq.matrix_strategy(SAMPLE_CI, "build")
        assert strategy is not None
        assert strategy.get("fail-fast") is False

    def test_matrix_include(self, yq):
        include = yq.matrix_include(SAMPLE_CI, "build")
        assert len(include) == 5

    def test_matrix_entry(self, yq):
        entry = yq.matrix_entry(SAMPLE_CI, "build", 0)
        assert entry["compiler"] == "gcc"

    def test_matrix_entry_out_of_range(self, yq):
        entry = yq.matrix_entry(SAMPLE_CI, "build", 999)
        assert entry == {}

    def test_matrix_count(self, yq):
        assert yq.matrix_count(SAMPLE_CI, "build") == 5

    def test_matrix_count_no_matrix(self, yq):
        assert yq.matrix_count(SAMPLE_CI, "changelog") == 0

    def test_matrix_filter_by_field(self, yq):
        gcc = yq.matrix_filter_by_field(SAMPLE_CI, "build", "compiler", "gcc")
        assert len(gcc) == 2  # GCC 15 + GCC 13


# =====================================================================
# Step queries
# =====================================================================


class TestYqStepQueries:
    def test_steps(self, yq):
        steps = yq.steps(SAMPLE_CI, "build")
        assert len(steps) == 3

    def test_step_count(self, yq):
        assert yq.step_count(SAMPLE_CI, "build") == 3

    def test_actions_used(self, yq):
        actions = yq.actions_used(SAMPLE_CI, "build")
        assert any("checkout" in a for a in actions)
        assert any("b2-workflow" in a for a in actions)


# =====================================================================
# Error handling
# =====================================================================


class TestYqErrors:
    def test_file_not_found(self, yq):
        with pytest.raises(FileNotFoundError):
            yq.workflow_name(Path("/nonexistent/file.yml"))

    def test_cache_clear(self, yq):
        # Load once to populate cache
        yq.workflow_name(SAMPLE_CI)
        yq.clear_cache()
        # Should work again (re-loads from disk)
        assert yq.workflow_name(SAMPLE_CI) == "CI Test"

    def test_version_without_yq(self, yq):
        if not yq.has_yq:
            assert yq.version() is None

    def test_query_without_yq(self, yq):
        if not yq.has_yq:
            with pytest.raises(YqNotFoundError):
                yq.query(SAMPLE_CI, ".name")


# =====================================================================
# Container queries
# =====================================================================


class TestYqContainerQueries:
    def test_job_container(self, yq):
        container = yq.job_container(SAMPLE_CI, "build")
        assert container is not None
        assert "image" in container

    def test_job_container_none(self, yq):
        container = yq.job_container(SAMPLE_CI, "changelog")
        assert container is None


# =====================================================================
# Platform-aware loading (yq-first on Linux)
# =====================================================================


class TestPlatformAwareLoading:
    """Verify yq-first loading behavior on Linux."""

    def test_is_linux_property(self, yq):
        import sys
        assert yq.is_linux == sys.platform.startswith("linux")

    def test_load_produces_valid_dict(self, yq):
        data = yq._load(SAMPLE_CI)
        assert isinstance(data, dict)
        assert "name" in data
        assert "jobs" in data

    def test_load_caches_result(self, yq):
        data1 = yq._load(SAMPLE_CI)
        data2 = yq._load(SAMPLE_CI)
        assert data1 is data2  # same object from cache

    def test_load_via_pyyaml_fallback(self, yq):
        """PyYAML fallback always works."""
        data = yq._load_via_pyyaml(SAMPLE_CI)
        assert isinstance(data, dict)
        assert data.get("name") == "CI Test"

    def test_load_via_yq_when_available(self, yq):
        """If yq binary is installed, _load_via_yq works."""
        if not yq.has_yq:
            pytest.skip("yq binary not installed")
        data = yq._load_via_yq(SAMPLE_CI)
        assert isinstance(data, dict)
        assert data.get("name") == "CI Test"

    def test_yq_and_pyyaml_produce_same_jobs(self, yq):
        """Both backends should produce equivalent job structures."""
        pyyaml_data = yq._load_via_pyyaml(SAMPLE_CI)
        pyyaml_jobs = list(pyyaml_data.get("jobs", {}).keys())

        if yq.has_yq:
            yq_data = yq._load_via_yq(SAMPLE_CI)
            yq_jobs = list(yq_data.get("jobs", {}).keys())
            assert pyyaml_jobs == yq_jobs
        else:
            pytest.skip("yq binary not installed -- cannot compare backends")
