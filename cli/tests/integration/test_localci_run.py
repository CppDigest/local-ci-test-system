"""Integration tests for ``localci run`` against real act/Docker."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
from localci.core.executor import JobStatus
from localci.core.results import ExecutionSummary

from .conftest import INTEGRATION_TIMEOUT

pytestmark = pytest.mark.integration

runner = CliRunner()


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


@pytest.mark.usefixtures("capy_image_tag")
def test_run_success(
    integration_project: Path,
    integration_logs_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(integration_project)
    result = _run_localci(
        integration_project,
        ".github/workflows/test.yml",
    )

    assert result.exit_code == 0, result.output

    last_run = integration_logs_dir / "last-run.json"
    assert last_run.exists(), "expected last-run.json after successful run"

    summary = ExecutionSummary.load(last_run)
    assert summary.all_passed
    assert summary.total == 1
    assert summary.results[0].status == JobStatus.PASSED


@pytest.mark.usefixtures("capy_image_tag")
def test_run_failure(
    integration_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(integration_project)
    result = _run_localci(
        integration_project,
        ".github/workflows/test-fail.yml",
    )

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output

    lower = result.output.lower()
    assert any(kw in lower for kw in ("failed", "error", "exit"))


def test_run_invalid_workflow(
    integration_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse errors occur before act/Docker; no capy_image_tag fixture required."""
    monkeypatch.chdir(integration_project)
    result = _run_localci(
        integration_project,
        ".github/workflows/invalid.yml",
    )

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    lower = result.output.lower()
    assert "parse" in lower or "workflow" in lower or "invalid" in lower
