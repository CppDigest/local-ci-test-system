"""Shared fixtures for act/Docker integration tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from localci.core.config import CAPY_NATIVE_IMAGE_PREFIX, GENERIC_NATIVE_IMAGE_PREFIX
from localci.core.executor import JobExecutor
from localci.core.image_tag import derive_image_tag
from localci.errors import DockerNotAvailableError

FIXTURE_PROJECT = (
    Path(__file__).resolve().parent.parent / "fixtures" / "integration" / "project"
)
ACT_RUNNER_IMAGE = "catthehacker/ubuntu:act-24.04"
INTEGRATION_JOB_ID = "test"
INTEGRATION_TIMEOUT = 180
DOCKER_PULL_TIMEOUT = 600
DOCKER_TAG_TIMEOUT = 60


def _act_available() -> bool:
    return JobExecutor().has_act


def _docker_available() -> bool:
    try:
        JobExecutor().check_docker()
        return True
    except DockerNotAvailableError:
        return False


@pytest.fixture(scope="session")
def require_act_and_docker() -> None:
    """Skip the entire integration session when act or Docker is unavailable."""
    if not _act_available():
        pytest.skip("act is not installed")
    if not _docker_available():
        pytest.skip("Docker is not available")


@pytest.fixture(scope="session")
def act_runner_image(require_act_and_docker: None) -> str:
    """Pull the act runner image used for ubuntu-latest jobs."""
    try:
        pull = subprocess.run(
            ["docker", "pull", ACT_RUNNER_IMAGE],
            capture_output=True,
            text=True,
            timeout=DOCKER_PULL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(
            f"timed out pulling {ACT_RUNNER_IMAGE} after {DOCKER_PULL_TIMEOUT}s"
        )
    if pull.returncode != 0:
        pytest.skip(
            f"could not pull {ACT_RUNNER_IMAGE}: "
            f"{pull.stderr.strip() or pull.stdout.strip()}"
        )
    return ACT_RUNNER_IMAGE


def _derived_tag_for_integration_workflow(native_image_prefix: str) -> str:
    from localci.core.workflow import WorkflowAnalyzer

    workflow_path = FIXTURE_PROJECT / ".github/workflows/test.yml"
    entry = WorkflowAnalyzer().analyze(workflow_path).jobs[INTEGRATION_JOB_ID].matrix[0]
    tag = derive_image_tag(entry, native_image_prefix=native_image_prefix)
    assert tag is not None
    return tag


def _tag_act_runner_image(act_runner_image: str, tag: str) -> str:
    try:
        tag_result = subprocess.run(
            ["docker", "tag", act_runner_image, tag],
            capture_output=True,
            text=True,
            timeout=DOCKER_TAG_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(
            f"timed out tagging {act_runner_image} as {tag} after {DOCKER_TAG_TIMEOUT}s"
        )
    if tag_result.returncode != 0:
        pytest.skip(
            f"could not tag {act_runner_image} as {tag}: "
            f"{tag_result.stderr.strip() or tag_result.stdout.strip()}"
        )
    return tag


@pytest.fixture(scope="session")
def derived_image_tag(act_runner_image: str, require_act_and_docker: None) -> str:
    """Tag the act runner as the generic-profile derived image for localci run."""
    tag = _derived_tag_for_integration_workflow(GENERIC_NATIVE_IMAGE_PREFIX)
    return _tag_act_runner_image(act_runner_image, tag)


@pytest.fixture(scope="session")
def capy_derived_image_tag(act_runner_image: str, require_act_and_docker: None) -> str:
    """Tag the act runner as the capy-profile derived image for localci run."""
    tag = _derived_tag_for_integration_workflow(CAPY_NATIVE_IMAGE_PREFIX)
    return _tag_act_runner_image(act_runner_image, tag)


@pytest.fixture
def integration_project(tmp_path: Path) -> tuple[Path, Path]:
    """Copy fixture project and return (project_root, logs_dir)."""
    dest = tmp_path / "project"
    shutil.copytree(FIXTURE_PROJECT, dest)

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    config_path = dest / ".localci.yml"
    config = yaml.safe_load(config_path.read_text())
    config["logging"]["directory"] = str(logs_dir)
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

    return dest, logs_dir


@pytest.fixture
def capy_integration_project(
    integration_project: tuple[Path, Path],
) -> tuple[Path, Path]:
    """Integration fixture project with ``patches.profile: capy``."""
    project, logs_dir = integration_project
    config_path = project / ".localci.yml"
    config = yaml.safe_load(config_path.read_text())
    patches = config.get("patches")
    if not isinstance(patches, dict):
        patches = {}
    patches["profile"] = "capy"
    config["patches"] = patches
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    return project, logs_dir
