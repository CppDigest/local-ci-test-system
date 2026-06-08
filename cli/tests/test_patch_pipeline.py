"""Tests for the configurable workflow patch pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from localci.cli.run.patcher import _write_patched_workflow
from localci.core.config import LocalCIConfig, PatchesConfig
from localci.core.patch_pipeline import PatchPipeline
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

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CAPY_WORKFLOW = FIXTURES_DIR / "capy" / ".github" / "workflows" / "ci.yml"


@pytest.fixture
def sample_entry() -> MatrixEntry:
    return MatrixEntry(
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


@pytest.fixture
def capy_workflow_path() -> Path:
    if not CAPY_WORKFLOW.exists():
        pytest.skip("capy workflow fixture not found")
    return CAPY_WORKFLOW


def test_pipeline_default_enables_all_steps(capy_workflow_path, sample_entry) -> None:
    """Default config runs the full patch pipeline (baseline behaviour)."""
    patched = _write_patched_workflow(capy_workflow_path, sample_entry)
    try:
        content = patched.read_text()
        assert "LOCALCI_B2_SOURCE_DIR" in content
        assert "Restore capy source file timestamps" in content
        assert "Skip b2 bootstrap" in content
    finally:
        patched.unlink(missing_ok=True)


def test_pipeline_disable_b2_patches(capy_workflow_path, sample_entry) -> None:
    """Disabling b2-related patches leaves the workflow unchanged for those steps."""
    cfg = LocalCIConfig(
        patches=PatchesConfig(
            b2_source_cache=False,
            restore_capy_timestamps=False,
            capy_copy_preservation=False,
            b2_bootstrap_skip=False,
        )
    )
    patched = _write_patched_workflow(
        capy_workflow_path, sample_entry, config=cfg
    )
    try:
        content = patched.read_text()
        assert "LOCALCI_B2_SOURCE_DIR" not in content
        assert "Restore capy source file timestamps" not in content
        assert "Skip b2 bootstrap" not in content
        assert "cp -rL boost-source boost-root" in content
    finally:
        patched.unlink(missing_ok=True)


def test_pipeline_disable_container_mounts(capy_workflow_path, sample_entry) -> None:
    mounts = "-v /host/boost:/tmp/localci-cache/boost"
    cfg = LocalCIConfig(patches=PatchesConfig(container_mounts=False))
    patched = _write_patched_workflow(
        capy_workflow_path,
        sample_entry,
        job_id="build",
        container_mount_options=mounts,
        config=cfg,
    )
    try:
        content = patched.read_text()
        assert "/host/boost:/tmp/localci-cache/boost" not in content
    finally:
        patched.unlink(missing_ok=True)


def test_pipeline_custom_order(capy_workflow_path, sample_entry) -> None:
    """Custom order only changes step sequence; all enabled steps still run."""
    cfg = LocalCIConfig(
        patches=PatchesConfig(
            order=[
                "codecov_skip",
                "b2_source_cache",
                "restore_capy_timestamps",
                "capy_copy_preservation",
                "b2_bootstrap_skip",
                "container_mounts",
                "image_substitution",
            ]
        )
    )
    pipeline = PatchPipeline.from_config(cfg)
    step_names = [step.name for step in pipeline._steps]
    assert step_names == [
        "codecov_skip",
        "b2_source_cache",
        "restore_capy_timestamps",
        "capy_copy_preservation",
        "b2_bootstrap_skip",
        "container_mounts",
        "image_substitution",
    ]


def test_patches_config_rejects_unknown_step() -> None:
    with pytest.raises(ValueError, match="Unknown patch steps"):
        PatchesConfig(order=["not_a_real_step"])
