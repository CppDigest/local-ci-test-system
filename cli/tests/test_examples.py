"""Smoke tests for the installation validation example under examples/."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
from localci.core.workflow import WorkflowAnalyzer

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATION_PROJECT = REPO_ROOT / "examples" / "validation-project"
WORKFLOW = VALIDATION_PROJECT / ".github" / "workflows" / "validate.yml"
CONFIG = VALIDATION_PROJECT / ".localci.yml"
REGISTRY = VALIDATION_PROJECT / "image-registry.yml"


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def validation_project_exists() -> None:
    if not VALIDATION_PROJECT.is_dir():
        pytest.skip(f"examples/validation-project not present at {VALIDATION_PROJECT}")
    for path in (WORKFLOW, CONFIG, REGISTRY):
        if not path.is_file():
            pytest.skip(f"missing example file: {path}")


def test_validation_workflow_parses(validation_project_exists: None) -> None:
    wf = WorkflowAnalyzer().analyze(WORKFLOW)
    assert "validate" in wf.jobs
    job = wf.jobs["validate"]
    assert len(job.matrix) == 1
    assert job.matrix[0].name == "Validation smoke"
    # runs-on must be present at the job level so act actually executes the container;
    # use a regex so equivalent quoting/spacing variants still pass.
    raw_workflow = WORKFLOW.read_text()
    assert re.search(
        r"runs-on:\s*['\"]?\$\{\{\s*matrix\.runs-on\s*\}\}['\"]?",
        raw_workflow,
    ), (
        "validate job must declare runs-on at the job level; "
        "without it act skips container execution"
    )


def test_validation_dry_run(
    validation_project_exists: None,
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(VALIDATION_PROJECT)
    result = cli_runner.invoke(
        cli,
        [
            "-c",
            str(CONFIG),
            "run",
            "--platform",
            "linux",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Validation smoke" in result.output or "validate" in result.output.lower()
