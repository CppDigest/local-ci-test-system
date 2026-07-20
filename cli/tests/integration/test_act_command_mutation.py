"""Integration tests for ActCommand mutation contract (JobExecutor side effects)."""

from __future__ import annotations

from pathlib import Path

import pytest

from localci.core.command_builder import ActCommandBuilder
from localci.core.executor import ActCommand, JobExecutor, JobStatus
from localci.core.workflow import MatrixEntry, WorkflowAnalyzer

from .conftest import INTEGRATION_JOB_ID, INTEGRATION_TIMEOUT

pytestmark = pytest.mark.integration


def _workflow_path(name: str, project_dir: Path) -> Path:
    return project_dir / ".github/workflows" / name


def _build_act_command(
    workflow_name: str,
    project_dir: Path,
    act_runner_image: str,
) -> tuple[MatrixEntry, ActCommand]:
    workflow_path = _workflow_path(workflow_name, project_dir)
    analyzer = WorkflowAnalyzer()
    workflow = analyzer.analyze(workflow_path)
    entry = workflow.jobs[INTEGRATION_JOB_ID].matrix[0]

    builder = ActCommandBuilder(
        workflow_file=workflow_path,
        project_dir=project_dir,
        job_id=INTEGRATION_JOB_ID,
        default_secrets={"GITHUB_TOKEN": "local-ci-test-token"},
    )
    cmd = builder.build(
        entry,
        image_tag=act_runner_image,
        # Minimal fixture matrix has no cc/cxx; supply env so executor
        # materializes an executor-owned env_file (mutation contract).
        extra_env={"LOCALCI_TEST_ENV": "mutation-contract"},
    )
    return entry, cmd


def test_act_command_mutation_and_cleanup(
    integration_project: tuple[Path, Path],
    act_runner_image: str,
) -> None:
    """End-to-end: executor mutates ActCommand during run and cleans temp files."""
    project, logs_dir = integration_project
    entry, cmd = _build_act_command("test.yml", project, act_runner_image)

    assert cmd.env, "fixture must populate env so executor materializes env_file"
    assert cmd.secrets, (
        "fixture must populate secrets so executor materializes secret_file"
    )
    assert cmd.env_file is None
    assert cmd.secret_file is None
    assert not cmd._executor_owned_env_file
    assert not cmd._executor_owned_secret_file
    assert cmd.event_file is not None
    assert cmd._executor_owned_event_file

    mutation_during_run = False

    def on_output(_line: str) -> None:
        nonlocal mutation_during_run
        if mutation_during_run:
            return
        env_ready = (
            cmd.env_file is not None
            and cmd._executor_owned_env_file
            and cmd.env_file.exists()
        )
        secret_ready = (
            cmd.secret_file is not None
            and cmd._executor_owned_secret_file
            and cmd.secret_file.exists()
        )
        if env_ready and secret_ready:
            mutation_during_run = True

    executor = JobExecutor(logs_dir=logs_dir)
    result = executor.run(
        cmd,
        matrix_index=entry.index,
        matrix_name=entry.name,
        timeout=INTEGRATION_TIMEOUT,
        stream_output=False,
        on_output=on_output,
    )

    assert result.status == JobStatus.PASSED
    assert mutation_during_run, (
        "temp files and ownership flags must be set before act output"
    )
    assert cmd._executor_owned_env_file
    assert cmd._executor_owned_secret_file
    assert cmd.env_file is not None
    assert cmd.secret_file is not None
    assert not cmd.env_file.exists(), (
        "executor-owned env file must be removed after run"
    )
    assert not cmd.secret_file.exists(), (
        "executor-owned secret file must be removed after run"
    )
    assert cmd.event_file is not None
    assert not cmd.event_file.exists(), (
        "executor-owned event file must be removed after run"
    )
