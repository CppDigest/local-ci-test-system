"""Tests for workflow patcher (run command): b2-source cache, Codecov, container options."""

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
def patched_workflow(capy_workflow_path, sample_entry):
    """Write patched workflow with optional args; yield path; unlink on teardown."""

    if not capy_workflow_path.exists():
        pytest.skip("capy workflow fixture not found")
    paths = []

    def _write(
        image_tag=None,
        job_id=None,
        container_mount_options=None,
    ):
        p = _write_patched_workflow(
            capy_workflow_path,
            sample_entry,
            image_tag=image_tag,
            job_id=job_id,
            container_mount_options=container_mount_options,
        )
        paths.append(p)
        return p

    yield _write
    for p in paths:
        p.unlink(missing_ok=True)


def test_patched_workflow_does_not_skip_boost_clone(patched_workflow):
    """boost-clone runs normally; no BOOST_ROOT conditional is injected onto that step."""
    patched = patched_workflow()
    content = patched.read_text()
    # boost-clone step must still be present and unconditional
    assert "boost-clone" in content
    assert "Clone Boost" in content
    # No skip-clone conditional from localci
    assert "env.BOOST_ROOT == ''" not in content
    assert "Use cached Boost (BOOST_ROOT)" not in content


def test_patched_workflow_preserves_boost_clone_step_structure(patched_workflow):
    """Patched workflow still has the Clone Boost and Patch Boost steps."""
    patched = patched_workflow()
    content = patched.read_text()
    assert "Clone Boost" in content
    assert "boost-clone" in content
    assert "Patch Boost" in content


def test_patched_workflow_uses_persistent_boost_root_cache(patched_workflow):
    """cp -rL boost-source boost-root is replaced with persistent b2-source cache logic.

    The rsync source must be boost-source (created by boost-clone, which still runs
    normally) — NOT $BOOST_ROOT (the host-side reference clone which may lack lib submodules).
    """
    patched = patched_workflow()
    content = patched.read_text()
    assert "LOCALCI_B2_SOURCE_DIR" in content
    assert "Jamroot" in content
    # Cache-hit: headers left untouched, only capy slot cleared and boost-root symlinked
    assert "ln -sfn" in content
    assert "libs/capy" in content
    # Cache-miss: standard cp -rL + seed cache from dereferenced boost-root (no symlinks)
    assert "cp -rL boost-source boost-root" in content
    assert "cp -a boost-root/." in content
    # No header copy on cache-hit (would reset timestamps and force full b2 rebuild)
    assert "cp -a boost-source/." not in content


def test_patched_workflow_restores_capy_timestamps(patched_workflow):
    """A restore-timestamps step is injected before Patch Boost.

    It reads b2-source/.capy-file-stats and, for each file whose sha256 matches
    the saved hash, restores the saved mtime.  Only files with different content
    keep their fresh checkout mtime, so b2 rebuilds exactly those files.
    """
    patched = patched_workflow()
    content = patched.read_text()
    assert "Restore capy source file timestamps" in content
    assert ".capy-file-stats" in content
    assert "sha256sum" in content
    assert "touch -d" in content
    # Step appears before Patch Boost
    restore_pos = content.index("Restore capy source file timestamps")
    patch_pos = content.index("Patch Boost")
    assert restore_pos < patch_pos


def test_patched_workflow_saves_capy_stats_and_uses_cp_rp(patched_workflow):
    """The cp -r in Patch Boost is replaced with cp -rp (preserves timestamps).

    A snapshot of capy C++ source file mtimes and sha256 hashes is saved to
    b2-source/.capy-file-stats so the next run can restore timestamps for
    unchanged files, enabling single-file incremental b2 builds.
    """
    patched = patched_workflow()
    content = patched.read_text()
    # Timestamps preserved on copy into boost-root
    assert "cp -rp" in content
    # Content-hash snapshot saved for next run
    assert ".capy-file-stats" in content
    assert "sha256sum" in content
    assert "stat -c" in content
    # Original cp -r (without p) should not be present for the capy copy
    assert 'cp -r "$workspace_root"' not in content


def test_patched_workflow_injects_b2_bootstrap_skip(patched_workflow):
    """A pre-step is injected before b2-workflow to stub bootstrap.sh when b2 is cached.

    Mirrors https://github.com/iTinkerBell/cpp-actions/commit/671009a but without
    requiring a fork: the stub is only applied when LOCALCI_B2_SOURCE_DIR/b2 exists.
    """
    patched = patched_workflow()
    content = patched.read_text()
    assert "Skip b2 bootstrap (b2 binary cached)" in content
    assert "LOCALCI_B2_SOURCE_DIR" in content
    assert "bootstrap.sh" in content
    assert "chmod +x boost-root/bootstrap.sh" in content
    # Step appears before the b2-workflow uses: line
    skip_pos = content.index("Skip b2 bootstrap")
    b2_wf_pos = content.index("b2-workflow")
    assert skip_pos < b2_wf_pos


def test_patched_workflow_injects_container_options(patched_workflow):
    """container.options gets cache -v mounts injected when container_mount_options set."""
    mounts = "-v /host/boost:/tmp/localci-cache/boost -v /host/ccache:/tmp/localci-cache/ccache"
    patched = patched_workflow(
        job_id="build",
        container_mount_options=mounts,
    )
    content = patched.read_text()
    assert "/host/boost:/tmp/localci-cache/boost" in content
    assert "/host/ccache:/tmp/localci-cache/ccache" in content
