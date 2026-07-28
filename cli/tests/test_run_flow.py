"""Unit tests for ``localci.cli.run.run_flow.execute_run``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from localci.cli.run.container import build_run_container
from localci.cli.run.params import RunOptions
from localci.cli.run.run_flow import _resolve_matrix_filters, execute_run
from localci.core.config import (
    CAPY_NATIVE_IMAGE_PREFIX,
    LocalCIConfig,
    MatrixConfig,
    MatrixFilter,
    PatchesConfig,
)
from localci.core.executor import ActNotFoundError, JobResult, JobStatus
from localci.core.models import PlatformOutcome
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
from localci.errors import WorkflowError

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_WORKFLOW = FIXTURES_DIR / "sample_workflow.yml"
PIPELINE_WORKFLOW = FIXTURES_DIR / "patcher" / "pipeline_minimal.yml"


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


def _passed_mock_run_results() -> dict[str, JobResult]:
    return {
        "build:0": JobResult(
            job_id="build",
            matrix_index=0,
            matrix_name="GCC 15",
            status=JobStatus.PASSED,
        )
    }


@pytest.fixture
def sample_config() -> LocalCIConfig:
    return LocalCIConfig(workflow=SAMPLE_WORKFLOW)


class TestResolveMatrixFilters:
    def test_cli_matrix_shadows_config_include(self) -> None:
        cfg = LocalCIConfig(
            workflow=SAMPLE_WORKFLOW,
            matrix=MatrixConfig(
                include=[MatrixFilter(compiler="gcc", version="15", name="GCC 15")]
            ),
        )
        options = _dry_run_options(
            matrix_filters=("compiler=clang", "version=20"),
        )
        matrix_include, matrix_exclude = _resolve_matrix_filters(options, cfg)
        assert matrix_include == [{"compiler": "clang", "version": "20"}]
        assert matrix_exclude is None

    def test_config_matrix_include_when_no_cli_filters(self) -> None:
        cfg = LocalCIConfig(
            workflow=SAMPLE_WORKFLOW,
            matrix=MatrixConfig(
                include=[MatrixFilter(compiler="gcc", version="15", name="GCC 15")]
            ),
        )
        options = _dry_run_options(matrix_filters=())
        matrix_include, matrix_exclude = _resolve_matrix_filters(options, cfg)
        assert matrix_include == [
            {"compiler": "gcc", "version": "15", "name": "GCC 15"}
        ]
        assert matrix_exclude is None

    def test_matrix_exclude_from_config(self) -> None:
        cfg = LocalCIConfig(
            workflow=SAMPLE_WORKFLOW,
            matrix=MatrixConfig(
                include=[MatrixFilter(compiler="gcc", version="15")],
                exclude=[MatrixFilter(compiler="clang", version="20")],
            ),
        )
        options = _dry_run_options(matrix_filters=("compiler=gcc",))
        matrix_include, matrix_exclude = _resolve_matrix_filters(options, cfg)
        assert matrix_include == [{"compiler": "gcc"}]
        assert matrix_exclude == [{"compiler": "clang", "version": "20"}]


class TestExecuteRunDryRun:
    """Direct tests of ``execute_run`` without act/Docker."""

    def test_dry_run_prints_execution_plan(self, sample_config: LocalCIConfig) -> None:
        with patch("localci.cli.run.run_flow._print_execution_plan") as mock_plan:
            code = execute_run(
                cfg=sample_config,
                options=_dry_run_options(),
                deps=build_run_container(),
            )
        assert code == 0
        mock_plan.assert_called_once()

    def test_capy_profile_passes_native_image_prefix_to_queue(self) -> None:
        """``run_flow`` must thread ``cfg.project.native_image_prefix`` into ``build()``."""
        cfg = LocalCIConfig(
            workflow=SAMPLE_WORKFLOW,
            patches=PatchesConfig(profile="capy"),
        )
        assert cfg.project.native_image_prefix == CAPY_NATIVE_IMAGE_PREFIX

        captured_queues: list = []

        def capture_plan(queue, workflow_path, timeout):
            captured_queues.append(queue)

        with patch(
            "localci.cli.run.run_flow._print_execution_plan",
            side_effect=capture_plan,
        ):
            code = execute_run(
                cfg=cfg,
                options=_dry_run_options(),
                deps=build_run_container(),
            )

        assert code == 0
        assert captured_queues
        runnable = [
            job
            for job in captured_queues[0].get_all_jobs()
            if job.platform_outcome == PlatformOutcome.RUN and job.image_tag
        ]
        assert runnable
        assert all(job.image_tag.startswith("capy-") for job in runnable)

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

    def test_empty_matrix_returns_zero(self, sample_config: LocalCIConfig) -> None:
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

    def test_binds_config_aware_workflow_patcher(
        self, sample_config: LocalCIConfig
    ) -> None:
        """``patches:`` settings must reach the orchestrator via ``make_workflow_patcher(cfg)``."""
        deps = build_run_container()
        mock_executor = MagicMock()
        deps.job_executor_factory = lambda _logs: mock_executor

        mock_manager = MagicMock()
        now = datetime.now()
        mock_run = MagicMock(all_passed=True)
        mock_run.execution_id = "test"
        mock_run.started_at = now
        mock_run.finished_at = now
        mock_run.results = _passed_mock_run_results()
        mock_manager.execute.return_value = mock_run
        deps.parallel_manager_factory = lambda **_kwargs: mock_manager

        mock_tracker = MagicMock()
        deps.progress_tracker_factory = lambda **_kwargs: mock_tracker

        captured: dict[str, object] = {}

        def capture_manager(**kwargs: object) -> MagicMock:
            captured["workflow_patcher"] = kwargs.get("workflow_patcher")
            return mock_manager

        deps.parallel_manager_factory = capture_manager

        with patch(
            "localci.cli.run.run_flow.make_workflow_patcher"
        ) as mock_make_patcher:
            bound_patcher = MagicMock()
            mock_make_patcher.return_value = bound_patcher
            code = execute_run(
                cfg=sample_config,
                options=_dry_run_options(dry_run=False, timeout=60),
                deps=deps,
            )

        assert code == 0
        mock_make_patcher.assert_called_once_with(sample_config)
        assert captured["workflow_patcher"] is bound_patcher

    def test_execute_run_propagates_disabled_patch_config(self) -> None:
        """``patches:`` toggles in cfg affect the real patcher passed to the orchestrator."""
        assert PIPELINE_WORKFLOW.is_file(), f"missing fixture: {PIPELINE_WORKFLOW}"
        cfg = LocalCIConfig(
            workflow=PIPELINE_WORKFLOW,
            patches=PatchesConfig(b2_bootstrap_skip=False),
        )
        entry = MatrixEntry(
            index=0,
            name="build (ubuntu-24.04, gcc-15)",
            platform=Platform.LINUX,
            compiler=CompilerInfo(
                family=CompilerFamily.GCC, version="15", cc="gcc-15", cxx="g++-15"
            ),
            container=ContainerInfo(image="ubuntu:24.04"),
            variant=BuildVariant(),
            packages=PackageRequirements(),
            runs_on="ubuntu-24.04",
            build_system=BuildSystem.B2,
            raw={},
        )

        deps = build_run_container()
        mock_executor = MagicMock()
        deps.job_executor_factory = lambda _logs: mock_executor

        now = datetime.now()
        mock_run = MagicMock(all_passed=True)
        mock_run.execution_id = "test"
        mock_run.started_at = now
        mock_run.finished_at = now
        mock_run.results = _passed_mock_run_results()
        mock_manager = MagicMock()
        mock_manager.execute.return_value = mock_run
        deps.progress_tracker_factory = lambda **_kwargs: MagicMock()

        captured: dict[str, object] = {}

        def capture_manager(**kwargs: object) -> MagicMock:
            captured["workflow_patcher"] = kwargs.get("workflow_patcher")
            return mock_manager

        deps.parallel_manager_factory = capture_manager

        code = execute_run(
            cfg=cfg,
            options=_dry_run_options(
                dry_run=False,
                timeout=60,
                workflow=str(PIPELINE_WORKFLOW),
            ),
            deps=deps,
        )
        assert code == 0

        patcher = captured["workflow_patcher"]
        assert callable(patcher)
        patched = patcher(PIPELINE_WORKFLOW, entry)  # type: ignore[operator]
        try:
            assert "Skip b2 bootstrap" not in patched.read_text()
        finally:
            patched.unlink(missing_ok=True)


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
