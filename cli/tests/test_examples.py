"""Smoke tests for the installation validation example under examples/."""

from __future__ import annotations

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

runner = CliRunner()


@pytest.fixture
def validation_project_exists() -> None:
    assert VALIDATION_PROJECT.is_dir(), f"missing {VALIDATION_PROJECT}"
    assert WORKFLOW.is_file()
    assert CONFIG.is_file()
    assert REGISTRY.is_file()


def test_validation_workflow_parses(validation_project_exists: None) -> None:
    wf = WorkflowAnalyzer().analyze(WORKFLOW)
    assert "validate" in wf.jobs
    assert len(wf.jobs["validate"].matrix) == 1
    assert wf.jobs["validate"].matrix[0].name == "Validation smoke"


def test_validation_dry_run(validation_project_exists: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(VALIDATION_PROJECT)
    result = runner.invoke(
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
