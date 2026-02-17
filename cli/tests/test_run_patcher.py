"""Tests for workflow patcher (run command): BOOST_ROOT skip, Codecov, etc."""

from __future__ import annotations

from pathlib import Path

import pytest

from localci.cli.run import _write_patched_workflow
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
def capy_workflow_path():
    return CAPY_WORKFLOW


@pytest.fixture
def sample_entry():
    """A matrix entry that would run the build job with boost-clone."""
    return MatrixEntry(
        index=0,
        name="build (ubuntu-24.04, gcc-15)",
        platform=Platform.LINUX,
        compiler=CompilerInfo(family=CompilerFamily.GCC, version="15", cc="gcc-15", cxx="g++-15"),
        container=ContainerInfo(image="ubuntu:24.04"),
        variant=BuildVariant(),
        packages=PackageRequirements(),
        runs_on="ubuntu-24.04",
        build_system=BuildSystem.B2,
        raw={},
    )


def test_patched_workflow_skips_boost_clone_when_boost_root_set(
    capy_workflow_path, sample_entry
):
    """When BOOST_ROOT is set (by localci), Clone Boost step gets if: env.BOOST_ROOT == ''."""
    if not capy_workflow_path.exists():
        pytest.skip("capy workflow fixture not found")
    patched = _write_patched_workflow(capy_workflow_path, sample_entry, image_tag=None)
    try:
        content = patched.read_text()
        assert "env.BOOST_ROOT == ''" in content
        assert "Use cached Boost (BOOST_ROOT)" in content
        assert "BOOST_ROOT != ''" in content
    finally:
        patched.unlink(missing_ok=True)


def test_patched_workflow_preserves_boost_clone_step_structure(
    capy_workflow_path, sample_entry
):
    """Patched workflow still has Clone Boost step and the new Use cached Boost step."""
    if not capy_workflow_path.exists():
        pytest.skip("capy workflow fixture not found")
    patched = _write_patched_workflow(capy_workflow_path, sample_entry, image_tag=None)
    try:
        content = patched.read_text()
        assert "Clone Boost" in content
        assert "boost-clone" in content
        assert "Patch Boost" in content
    finally:
        patched.unlink(missing_ok=True)
