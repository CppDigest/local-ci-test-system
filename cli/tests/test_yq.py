"""Unit tests for the YqWrapper (utils/yq.py).

Tests use the PyYAML fallback when ``yq`` is not installed, and yq
subprocess when it is.  All high-level methods go through ``query()``.
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
        assert "master" in branches

    def test_global_env(self, yq):
        env = yq.global_env(SAMPLE_CI)
        assert env["NET_RETRY_COUNT"] == "5"
        assert env["GIT_FETCH_JOBS"] == "8"

    def test_concurrency(self, yq):
        conc = yq.concurrency(SAMPLE_CI)
        assert conc is not None
        assert conc.get("cancel-in-progress") is True


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
        assert len(include) == 14

    def test_matrix_entry(self, yq):
        entry = yq.matrix_entry(SAMPLE_CI, "build", 0)
        assert entry["compiler"] == "msvc"

    def test_matrix_entry_out_of_range(self, yq):
        entry = yq.matrix_entry(SAMPLE_CI, "build", 999)
        assert entry == {}

    def test_matrix_count(self, yq):
        assert yq.matrix_count(SAMPLE_CI, "build") == 14

    def test_matrix_count_no_matrix(self, yq):
        assert yq.matrix_count(SAMPLE_CI, "changelog") == 0

    def test_matrix_filter_by_field(self, yq):
        gcc = yq.matrix_filter_by_field(SAMPLE_CI, "build", "compiler", "gcc")
        assert len(gcc) == 4  # GCC 15, GCC 15 asan, GCC 12, GCC 13


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
        yq.workflow_name(SAMPLE_CI)
        yq.clear_cache()
        assert yq.workflow_name(SAMPLE_CI) == "CI Test"

    def test_version_without_yq(self, yq):
        if not yq.has_yq:
            assert yq.version() is None

    def test_has_yq_property(self, yq):
        # Just verify property is accessible
        assert isinstance(yq.has_yq, bool)


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
