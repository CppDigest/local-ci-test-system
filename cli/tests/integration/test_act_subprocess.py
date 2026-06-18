"""Integration tests for JobExecutor + act subprocess boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from localci.core.command_builder import ActCommandBuilder
from localci.core.executor import JobExecutor, JobResult, JobStatus
from localci.core.workflow import MatrixEntry, WorkflowAnalyzer

from .conftest import INTEGRATION_JOB_ID, INTEGRATION_TIMEOUT

pytestmark = pytest.mark.integration


def _workflow_path(name: str, project_dir: Path) -> Path:
    return project_dir / ".github/workflows" / name


def _build_and_run(
    workflow_name: str,
    logs_dir: Path,
    act_runner_image: str,
    project_dir: Path,
    default_secrets: dict[str, str] | None = None,
) -> tuple[JobResult, MatrixEntry]:
    workflow_path = _workflow_path(workflow_name, project_dir)
    analyzer = WorkflowAnalyzer()
    workflow = analyzer.analyze(workflow_path)
    entry = workflow.jobs[INTEGRATION_JOB_ID].matrix[0]

    builder = ActCommandBuilder(
        workflow_file=workflow_path,
        project_dir=project_dir,
        job_id=INTEGRATION_JOB_ID,
        default_secrets=default_secrets or {},
    )
    cmd = builder.build(entry, image_tag=act_runner_image)
    executor = JobExecutor(logs_dir=logs_dir)
    result = executor.run(
        cmd,
        matrix_index=entry.index,
        matrix_name=entry.name,
        timeout=INTEGRATION_TIMEOUT,
        stream_output=False,
    )
    return result, entry


def test_successful_job_execution(
    integration_project: tuple[Path, Path],
    act_runner_image: str,
) -> None:
    project, logs_dir = integration_project
    result, _ = _build_and_run("test.yml", logs_dir, act_runner_image, project)

    assert result.status == JobStatus.PASSED
    assert result.exit_code == 0
    assert result.stdout or result.stderr
    assert result.log_file is not None
    assert result.log_file.exists()


def test_failing_job_extract_error(
    integration_project: tuple[Path, Path],
    act_runner_image: str,
) -> None:
    project, logs_dir = integration_project
    result, _ = _build_and_run("test-fail.yml", logs_dir, act_runner_image, project)

    assert result.status == JobStatus.FAILED
    assert result.exit_code is not None
    assert result.exit_code != 0

    captured = result.stderr or result.stdout
    assert captured.strip()

    extracted = JobExecutor._extract_error(captured)
    assert result.error_message == extracted
    assert result.error_message is not None

    lower = result.error_message.lower()
    assert any(kw in lower for kw in ("failed", "error", "exit"))


def test_github_token_available_as_workflow_secret(
    integration_project: tuple[Path, Path],
    act_runner_image: str,
) -> None:
    project, logs_dir = integration_project
    result, _ = _build_and_run(
        "token-test.yml",
        logs_dir,
        act_runner_image,
        project,
        default_secrets={"GITHUB_TOKEN": "local-ci-test-token"},
    )

    assert result.status == JobStatus.PASSED
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    lines = combined.splitlines()
    # act logs the step script (including echo "TOKEN_MISSING") in combined output;
    # assert on container stdout lines prefixed with "| " instead.
    assert any("| TOKEN_PRESENT" in line for line in lines)
    assert not any("| TOKEN_MISSING" in line for line in lines)
