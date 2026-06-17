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
PIPELINE_WORKFLOW = FIXTURES_DIR / "patcher" / "pipeline_minimal.yml"


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
def workflow_path() -> Path:
    assert PIPELINE_WORKFLOW.is_file(), f"missing fixture: {PIPELINE_WORKFLOW}"
    return PIPELINE_WORKFLOW


def test_pipeline_default_enables_all_steps(workflow_path, sample_entry) -> None:
    """Default config runs the full patch pipeline (baseline behaviour)."""
    patched = _write_patched_workflow(workflow_path, sample_entry)
    try:
        content = patched.read_text()
        assert "LOCALCI_B2_SOURCE_DIR" in content
        assert "Restore capy source file timestamps" in content
        assert "Skip b2 bootstrap" in content
    finally:
        patched.unlink(missing_ok=True)


def test_pipeline_disable_b2_patches(workflow_path, sample_entry) -> None:
    """Disabling b2-related patches leaves the workflow unchanged for those steps."""
    cfg = LocalCIConfig(
        patches=PatchesConfig(
            b2_source_cache=False,
            restore_capy_timestamps=False,
            capy_copy_preservation=False,
            b2_bootstrap_skip=False,
        )
    )
    patched = _write_patched_workflow(workflow_path, sample_entry, config=cfg)
    try:
        content = patched.read_text()
        assert "LOCALCI_B2_SOURCE_DIR" not in content
        assert "Restore capy source file timestamps" not in content
        assert "Skip b2 bootstrap" not in content
        assert "cp -rL boost-source boost-root" in content
    finally:
        patched.unlink(missing_ok=True)


def test_pipeline_disable_container_mounts(workflow_path, sample_entry) -> None:
    mounts = "-v /host/boost:/tmp/localci-cache/boost"
    mount_fragment = "/host/boost:/tmp/localci-cache/boost"

    patched_enabled = _write_patched_workflow(
        workflow_path,
        sample_entry,
        job_id="build",
        container_mount_options=mounts,
        config=LocalCIConfig(patches=PatchesConfig(container_mounts=True)),
    )
    try:
        assert mount_fragment in patched_enabled.read_text()
    finally:
        patched_enabled.unlink(missing_ok=True)

    patched_disabled = _write_patched_workflow(
        workflow_path,
        sample_entry,
        job_id="build",
        container_mount_options=mounts,
        config=LocalCIConfig(patches=PatchesConfig(container_mounts=False)),
    )
    try:
        assert mount_fragment not in patched_disabled.read_text()
    finally:
        patched_disabled.unlink(missing_ok=True)


def test_pipeline_custom_order() -> None:
    """Custom order lists all enabled steps in the requested sequence."""
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
    step_names = [step.name for step in pipeline.steps]
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


def test_patches_config_rejects_duplicate_step() -> None:
    with pytest.raises(ValueError, match="Duplicate step names"):
        PatchesConfig(
            order=[
                "b2_source_cache",
                "b2_source_cache",
                "codecov_skip",
                "restore_capy_timestamps",
                "capy_copy_preservation",
                "b2_bootstrap_skip",
                "container_mounts",
                "image_substitution",
            ]
        )


def test_patches_config_rejects_empty_order() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        PatchesConfig(order=[])


def test_from_config_raises_when_step_missing_from_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from localci.core.patch_steps import PATCH_STEP_REGISTRY

    incomplete = {
        name: cls
        for name, cls in PATCH_STEP_REGISTRY.items()
        if name != "b2_source_cache"
    }
    monkeypatch.setattr("localci.core.patch_steps.PATCH_STEP_REGISTRY", incomplete)
    with pytest.raises(ValueError, match="b2_source_cache"):
        PatchPipeline.from_config(LocalCIConfig())


def test_patches_config_rejects_enabled_step_missing_from_order() -> None:
    with pytest.raises(ValueError, match="missing from 'order'"):
        PatchesConfig(
            b2_source_cache=True,
            order=[
                "codecov_skip",
                "restore_capy_timestamps",
                "capy_copy_preservation",
                "b2_bootstrap_skip",
                "container_mounts",
                "image_substitution",
            ],
        )
