"""Unit tests for decomposed ``localci run`` orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from localci.cli.run.container import RunDependencies, build_run_container
from localci.cli.run.orchestrator import execute_run
from localci.cli.run.params import RunOptions
from localci.core.config import LocalCIConfig
from localci.core.executor import ActNotFoundError

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_WORKFLOW = FIXTURES_DIR / "sample_workflow.yml"


@pytest.fixture
def sample_config() -> LocalCIConfig:
    return LocalCIConfig(workflow=SAMPLE_WORKFLOW)


@pytest.fixture
def click_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.exit = MagicMock(side_effect=SystemExit)
    return ctx


class TestExecuteRunDryRun:
    """Direct tests of ``execute_run`` without act/Docker."""

    def test_dry_run_prints_execution_plan(
        self, sample_config: LocalCIConfig, click_ctx: MagicMock
    ) -> None:
        execute_run(
            ctx=click_ctx,
            cfg=sample_config,
            options=RunOptions(
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
            ),
            deps=build_run_container(),
        )
        click_ctx.exit.assert_not_called()

    def test_dry_run_skips_preflight_and_parallel_execute(
        self, sample_config: LocalCIConfig, click_ctx: MagicMock
    ) -> None:
        deps = build_run_container()
        mock_executor = MagicMock()
        mock_executor.check_act.side_effect = ActNotFoundError()
        deps.job_executor_factory = lambda _logs: mock_executor

        with patch(
            "localci.cli.run.orchestrator._print_execution_plan"
        ) as mock_plan:
            execute_run(
                ctx=click_ctx,
                cfg=sample_config,
                options=RunOptions(
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
                ),
                deps=deps,
            )

        mock_plan.assert_called_once()
        mock_executor.check_act.assert_not_called()

    def test_act_not_found_exits_when_not_dry_run(
        self, sample_config: LocalCIConfig, click_ctx: MagicMock
    ) -> None:
        deps = build_run_container()
        mock_executor = MagicMock()
        mock_executor.check_act.side_effect = ActNotFoundError()
        deps.job_executor_factory = lambda _logs: mock_executor

        with pytest.raises(SystemExit):
            execute_run(
                ctx=click_ctx,
                cfg=sample_config,
                options=RunOptions(
                    workflow=str(SAMPLE_WORKFLOW),
                    jobs=(),
                    platform=None,
                    compiler=None,
                    matrix_filters=(),
                    parallel=None,
                    timeout=60,
                    dry_run=False,
                    no_cache=True,
                    cache_dir=None,
                    rebuild_image=False,
                    keep_containers=None,
                    interactive=False,
                    verbose=False,
                    github_token=None,
                    offline=False,
                ),
                deps=deps,
            )

        click_ctx.exit.assert_called_once_with(1)


class TestExecuteRunCliParity:
    """CLI still delegates to the same orchestration path."""

    def test_cli_dry_run_via_package_entry(self) -> None:
        from localci.cli.main import cli

        result = CliRunner().invoke(
            cli, ["run", "--workflow", str(SAMPLE_WORKFLOW), "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output or "execution plan" in result.output.lower()
