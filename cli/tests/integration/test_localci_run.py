"""Integration tests for ``localci run`` against real act/Docker."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
from localci.core.executor import JobExecutor, JobStatus
from localci.core.results import ExecutionSummary

from .conftest import INTEGRATION_TIMEOUT

pytestmark = pytest.mark.integration

runner = CliRunner()


def _executor_capture_from_log_text(log_text: str) -> str:
    """Approximate the stderr-or-stdout string passed to ``_extract_error``.

    Job logs interleave streams and include act summary lines that are not in
    the stderr-only capture the executor uses when stderr is non-empty.
    """
    body_lines = [line for line in log_text.splitlines() if not line.startswith("#")]
    stderr_like = [
        line
        for line in body_lines
        if "error:" in line.lower() or "fatal:" in line.lower()
    ]
    if stderr_like:
        return "\n".join(stderr_like)
    return "\n".join(body_lines)


def _run_localci(
    project: Path,
    workflow: str,
    extra_args: list[str] | None = None,
) -> object:
    args = [
        "-c",
        str(project / ".localci.yml"),
        "run",
        "--workflow",
        workflow,
        "--no-cache",
        "--parallel",
        "1",
        "--timeout",
        str(INTEGRATION_TIMEOUT),
    ]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(cli, args)


@pytest.mark.usefixtures("derived_image_tag")
def test_run_success(
    integration_project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, logs_dir = integration_project
    monkeypatch.chdir(project)
    result = _run_localci(project, ".github/workflows/test.yml")

    assert result.exit_code == 0, result.output

    last_run = logs_dir / "last-run.json"
    assert last_run.exists(), "expected last-run.json after successful run"

    summary = ExecutionSummary.load(last_run)
    assert summary.all_passed
    assert summary.total == 1
    assert summary.results[0].status == JobStatus.PASSED


@pytest.mark.usefixtures("derived_image_tag")
def test_run_failure(
    integration_project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, logs_dir = integration_project
    monkeypatch.chdir(project)
    result = _run_localci(project, ".github/workflows/test-fail.yml")

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output

    last_run = logs_dir / "last-run.json"
    assert last_run.exists(), "expected last-run.json after failed run"

    summary = ExecutionSummary.load(last_run)
    assert summary.total == 1
    job = summary.results[0]
    assert job.status == JobStatus.FAILED
    assert job.error_message

    if job.log_file and job.log_file.exists():
        log_text = job.log_file.read_text(encoding="utf-8", errors="replace")
        captured = _executor_capture_from_log_text(log_text)
        assert job.error_message in JobExecutor._extract_error(captured)
        assert job.error_message.splitlines()[0] in log_text
    else:
        assert job.error_message in JobExecutor._extract_error(result.output)


def test_run_invalid_workflow(
    integration_project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow parse fails before act/Docker; intentionally omits require_act_and_docker.

    Kept in this package (not unit tests) to assert the full CLI path exits cleanly
    without a traceback when given a malformed workflow file.
    """
    project, _logs_dir = integration_project
    monkeypatch.chdir(project)
    result = _run_localci(project, ".github/workflows/invalid.yml")

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    lower = result.output.lower()
    assert "parse" in lower or "workflow" in lower or "invalid" in lower
