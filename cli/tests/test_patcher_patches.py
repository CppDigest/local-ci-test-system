"""Unit tests for _write_patched_workflow patch types (issue #50).

Each patch type has positive and negative cases using minimal YAML fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "patcher"


def _make_entry(name: str = "GCC 15: C++20") -> MatrixEntry:
    return MatrixEntry(
        index=0,
        name=name,
        platform=Platform.LINUX,
        compiler=CompilerInfo(
            family=CompilerFamily.GCC,
            version="15",
            cc="gcc-15",
            cxx="g++-15",
        ),
        container=ContainerInfo(image="ubuntu:25.04"),
        variant=BuildVariant(),
        packages=PackageRequirements(),
        runs_on="ubuntu-latest",
        build_system=BuildSystem.B2,
        raw={},
    )


@pytest.fixture
def patcher_paths():
    """Track patched temp files and unlink on teardown."""
    paths: list[Path] = []

    def _patch(workflow_path: Path, entry: MatrixEntry | None = None, **kwargs) -> Path:
        patched = _write_patched_workflow(
            workflow_path,
            entry or _make_entry(),
            **kwargs,
        )
        paths.append(patched)
        return patched

    yield _patch
    for path in paths:
        path.unlink(missing_ok=True)


def _assert_valid_yaml(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    yaml.safe_load(content)
    return content


# ---------------------------------------------------------------------------
# container image substitution (matrix entry targeting)
# ---------------------------------------------------------------------------


class TestContainerImagePatch:
    def test_positive_replaces_container_for_named_matrix_entry(self, patcher_paths):
        workflow = FIXTURES_DIR / "container_image.yml"
        original = workflow.read_text(encoding="utf-8")
        patched = patcher_paths(
            workflow,
            _make_entry("GCC 15: C++20"),
            image_tag="localci/gcc-15:custom",
        )
        content = _assert_valid_yaml(patched)

        assert 'container: "localci/gcc-15:custom"' in content
        assert 'container: "ubuntu:22.04"' in content
        assert "runs-on: ubuntu-latest" in content
        assert original.count("runs-on: ubuntu-latest") == content.count(
            "runs-on: ubuntu-latest"
        )

    def test_negative_raises_when_matrix_entry_name_missing(self, patcher_paths):
        workflow = FIXTURES_DIR / "container_image.yml"
        with pytest.raises(
            ValueError, match="Matrix entry name 'No Such Entry' not found"
        ):
            patcher_paths(
                workflow,
                _make_entry("No Such Entry"),
                image_tag="localci/missing:latest",
            )

    def test_negative_skips_when_image_tag_not_provided(self, patcher_paths):
        workflow = FIXTURES_DIR / "container_image.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert 'container: "ubuntu:25.04"' in content
        assert "localci/" not in content


# ---------------------------------------------------------------------------
# container mount options (cache injection)
# ---------------------------------------------------------------------------


class TestContainerMountPatch:
    @pytest.mark.parametrize(
        ("fixture_name", "has_existing_options"),
        [
            ("container_mount.yml", True),
            ("container_mount_no_options.yml", False),
        ],
    )
    def test_positive_injects_mount_options(
        self, patcher_paths, fixture_name, has_existing_options
    ):
        mounts = "-v /host/boost:/tmp/localci-cache/boost"
        workflow = FIXTURES_DIR / fixture_name
        patched = patcher_paths(
            workflow,
            job_id="build",
            container_mount_options=mounts,
        )
        content = _assert_valid_yaml(patched)

        assert mounts in content
        assert "options:" in content
        if has_existing_options:
            assert "--privileged" in content

    def test_negative_skips_without_job_id(self, patcher_paths):
        workflow = FIXTURES_DIR / "container_mount.yml"
        original = workflow.read_text(encoding="utf-8")
        patched = patcher_paths(
            workflow,
            container_mount_options="-v /host/boost:/cache",
        )
        content = _assert_valid_yaml(patched)
        assert content == original.replace("\r\n", "\n")

    def test_negative_skips_without_mount_options(self, patcher_paths):
        workflow = FIXTURES_DIR / "container_mount.yml"
        original = workflow.read_text(encoding="utf-8")
        patched = patcher_paths(workflow, job_id="build")
        content = _assert_valid_yaml(patched)
        assert content == original.replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# boost-source cache (cp -rL replacement)
# ---------------------------------------------------------------------------


class TestBoostCachePatch:
    def test_positive_replaces_cp_with_cache_logic(self, patcher_paths):
        workflow = FIXTURES_DIR / "boost_cache.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)

        assert "LOCALCI_B2_SOURCE_DIR" in content
        assert "Jamroot" in content
        assert "ln -sfn" in content
        assert "cp -a boost-root/." in content

    def test_negative_leaves_workflow_unchanged_without_boost_copy_line(
        self, patcher_paths
    ):
        workflow = FIXTURES_DIR / "container_image.yml"
        original = workflow.read_text(encoding="utf-8")
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert "LOCALCI_B2_SOURCE_DIR" not in content
        assert "cp -rL boost-source boost-root" not in content
        assert content == original.replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# capy timestamp restore (injected before Patch Boost)
# ---------------------------------------------------------------------------


class TestCapyTimestampsPatch:
    def test_positive_injects_restore_step_before_patch_boost(self, patcher_paths):
        workflow = FIXTURES_DIR / "boost_cache.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)

        assert "Restore capy source file timestamps" in content
        assert ".capy-file-stats" in content
        assert content.index("Restore capy source file timestamps") < content.index(
            "Patch Boost"
        )

    def test_negative_skips_duplicate_on_already_patched_workflow(self, patcher_paths):
        workflow = FIXTURES_DIR / "already_patched.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert content.count("Restore capy source file timestamps") == 1


# ---------------------------------------------------------------------------
# capy copy + stats (cp -r -> cp -rp)
# ---------------------------------------------------------------------------


class TestCapyCopyPatch:
    def test_positive_replaces_cp_r_with_cp_rp_and_stats(self, patcher_paths):
        workflow = FIXTURES_DIR / "capy_copy.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)

        assert 'cp -rp "$workspace_root"/capy-root "libs/$module"' in content
        assert ".capy-file-stats" in content
        assert "sha256sum" in content
        assert 'cp -r "$workspace_root"' not in content

    def test_negative_no_cp_rp_injected_when_capy_copy_line_absent(self, patcher_paths):
        workflow = FIXTURES_DIR / "boost_cache.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert 'cp -rp "$workspace_root"' not in content


# ---------------------------------------------------------------------------
# b2 bootstrap skip
# ---------------------------------------------------------------------------


class TestB2BootstrapPatch:
    def test_positive_injects_skip_step_before_b2_workflow(self, patcher_paths):
        workflow = FIXTURES_DIR / "b2_workflow.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)

        assert "Skip b2 bootstrap (b2 binary cached)" in content
        assert "bootstrap.sh" in content
        assert content.index("Skip b2 bootstrap") < content.index("b2-workflow")

    def test_negative_skips_when_b2_workflow_step_absent(self, patcher_paths):
        workflow = FIXTURES_DIR / "boost_cache.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert "Skip b2 bootstrap (b2 binary cached)" not in content


# ---------------------------------------------------------------------------
# codecov skip under act
# ---------------------------------------------------------------------------


class TestCodecovPatch:
    def test_positive_wraps_upload_with_act_guard(self, patcher_paths):
        workflow = FIXTURES_DIR / "codecov.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)

        assert 'if [ -z "${ACT:-}" ] || [ "$ACT" != "true" ]; then' in content
        assert "Skipping Codecov upload (running under act)" in content
        assert "https://codecov.io/bash" in content

    def test_negative_leaves_workflow_without_codecov_unchanged(self, patcher_paths):
        workflow = FIXTURES_DIR / "boost_cache.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert "codecov.io" not in content
        assert "ACT" not in content
        assert "LOCALCI_B2_SOURCE_DIR" in content


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


class TestPatcherEdgeCases:
    def test_empty_matrix_produces_valid_yaml(self, patcher_paths):
        workflow = FIXTURES_DIR / "empty_matrix.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert "include: []" in content
        assert "echo no-matrix" in content

    def test_missing_runs_on_preserves_unrelated_sections(self, patcher_paths):
        workflow = FIXTURES_DIR / "no_runs_on.yml"
        patched = patcher_paths(workflow)
        content = _assert_valid_yaml(patched)
        assert "KEEP_ME: unchanged" in content
        assert "runs-on" not in content
        assert "LOCALCI_B2_SOURCE_DIR" in content

    def test_repatch_preserves_idempotent_patches_and_valid_yaml(self, patcher_paths):
        """Timestamp restore is idempotent; re-patched output stays valid YAML."""
        workflow = FIXTURES_DIR / "already_patched.yml"
        first = patcher_paths(workflow)
        second = patcher_paths(first, _make_entry())
        content = _assert_valid_yaml(second)
        assert content.count("Restore capy source file timestamps") == 1

        b2_workflow = FIXTURES_DIR / "b2_workflow.yml"
        first_b2 = patcher_paths(b2_workflow)
        second_b2 = patcher_paths(first_b2, _make_entry())
        b2_content = _assert_valid_yaml(second_b2)
        assert b2_content.count("Skip b2 bootstrap (b2 binary cached)") == 1


@pytest.mark.parametrize(
    ("fixture_name", "patch_kwargs"),
    [
        ("container_image.yml", {"image_tag": "localci/test:latest"}),
        (
            "container_mount.yml",
            {"job_id": "build", "container_mount_options": "-v /cache:/cache"},
        ),
        (
            "container_mount_no_options.yml",
            {"job_id": "build", "container_mount_options": "-v /cache:/cache"},
        ),
        ("boost_cache.yml", {}),
        ("capy_copy.yml", {}),
        ("b2_workflow.yml", {}),
        ("codecov.yml", {}),
        ("already_patched.yml", {}),
        ("empty_matrix.yml", {}),
        ("no_runs_on.yml", {}),
    ],
)
def test_all_fixtures_produce_valid_yaml(patcher_paths, fixture_name, patch_kwargs):
    """Every minimal fixture remains parseable after a full patch pass."""
    workflow = FIXTURES_DIR / fixture_name
    patched = patcher_paths(workflow, **patch_kwargs)
    _assert_valid_yaml(patched)
