"""Unit tests for ``localci.cli.run.run_flow.execute_run``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from localci.cli.run.container import RunDependencies, build_run_container
from localci.cli.run.params import RunOptions
from localci.cli.run.run_flow import execute_run
from localci.core.config import LocalCIConfig
from localci.core.executor import ActNotFoundError
from localci.errors import WorkflowError

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_WORKFLOW = FIXTURES_DIR / "sample_workflow.yml"


def _dry_run_options(**overrides: object) -> RunOptions:
    base = dict(
        workflow=str(SAMPLE_WORKFLOW),
        jobs=(),
        platform=None,
        compiler=None,
        matrix_filters=(),
        parallel=None,
        timeout=None,
        dry_run=True,
        no_cache=True,
        cache_dir=None,
        rebuild_image=False,
        keep_containers=None,
        interactive=False,
        verbose=False,
        github_token=None,
        offline=False,
    )
    base.update(overrides)
    return RunOptions(**base)  # type: ignore[arg-type]


@pytest.fixture
def sample_config() -> LocalCIConfig:
    return LocalCIConfig(workflow=SAMPLE_WORKFLOW)


class TestExecuteRunDryRun:
    """Direct tests of ``execute_run`` without act/Docker."""

    def test_dry_run_prints_execution_plan(
        self, sample_config: LocalCIConfig
    ) -> None:
        with patch("localci.cli.run.run_flow._print_execution_plan") as mock_plan:
            code = execute_run(
                cfg=sample_config,
                options=_dry_run_options(),
                deps=build_run_container(),
            )
        assert code == 0
        mock_plan.assert_called_once()

    def test_dry_run_skips_preflight_and_parallel_execute(
        self, sample_config: LocalCIConfig
    ) -> None:
        deps = build_run_container()
        mock_executor = MagicMock()
        mock_executor.check_act.side_effect = ActNotFoundError()
        deps.job_executor_factory = lambda _logs: mock_executor

        with patch("localci.cli.run.run_flow._print_execution_plan"):
            code = execute_run(
                cfg=sample_config,
                options=_dry_run_options(),
                deps=deps,
            )

        assert code == 0
        mock_executor.check_act.assert_not_called()

    def test_act_not_found_returns_exit_code_when_not_dry_run(
        self, sample_config: LocalCIConfig
    ) -> None:
        deps = build_run_container()
        mock_executor = MagicMock()
        mock_executor.check_act.side_effect = ActNotFoundError()
        deps.job_executor_factory = lambda _logs: mock_executor

        code = execute_run(
            cfg=sample_config,
            options=_dry_run_options(dry_run=False, timeout=60),
            deps=deps,
        )

        assert code == 1

    def test_workflow_error_returns_exit_code(
        self, sample_config: LocalCIConfig
    ) -> None:
        deps = build_run_container()
        deps.workflow_analyzer = MagicMock()
        deps.workflow_analyzer.analyze.side_effect = WorkflowError("invalid workflow")

        code = execute_run(
            cfg=sample_config,
            options=_dry_run_options(),
            deps=deps,
        )

        assert code == 1

    def test_empty_matrix_returns_zero(
        self, sample_config: LocalCIConfig
    ) -> None:
        deps = build_run_container()
        mock_wf = MagicMock()
        mock_wf.jobs = {}
        deps.workflow_analyzer = MagicMock()
        deps.workflow_analyzer.analyze.return_value = mock_wf

        code = execute_run(
            cfg=sample_config,
            options=_dry_run_options(),
            deps=deps,
        )

        assert code == 0

    def test_no_jobs_match_filters_returns_zero(
        self, sample_config: LocalCIConfig
    ) -> None:
        code = execute_run(
            cfg=sample_config,
            options=_dry_run_options(jobs=("nonexistent-job-xyz",)),
            deps=build_run_container(),
        )
        assert code == 0


class TestExecuteRunCliParity:
    """CLI still delegates to the same orchestration path."""

    def test_cli_dry_run_via_package_entry(self) -> None:
        from localci.cli.main import cli

        result = CliRunner().invoke(
            cli, ["run", "--workflow", str(SAMPLE_WORKFLOW), "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output or "execution plan" in result.output.lower()

    def test_cli_dry_run_offline_skips_token_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from localci.cli.main import cli

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = CliRunner().invoke(
            cli,
            [
                "run",
                "--workflow",
                str(SAMPLE_WORKFLOW),
                "--dry-run",
                "--offline",
            ],
        )
        assert result.exit_code == 0
        assert "No GitHub token provided" not in result.output
