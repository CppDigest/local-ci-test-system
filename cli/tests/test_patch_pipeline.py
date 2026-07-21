"""Tests for the configurable workflow patch pipeline."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from localci.cli.run.patcher import _write_patched_workflow
from localci.core.config import LocalCIConfig, PatchesConfig, PatchProjectConfig
from localci.core.patch_pipeline import PatchContext, PatchPipeline, PatchStep
from localci.core.patch_steps import ContainerMountsStep
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
GENERIC_CPP_WORKFLOW = FIXTURES_DIR / "patcher" / "generic_cpp.yml"


def _capy_config() -> LocalCIConfig:
    return LocalCIConfig(patches=PatchesConfig(profile="capy"))


def _generic_project_config() -> LocalCIConfig:
    return LocalCIConfig(
        patches=PatchesConfig(
            b2_source_cache=True,
            restore_capy_timestamps=True,
            capy_copy_preservation=True,
            b2_bootstrap_skip=True,
            project=PatchProjectConfig(
                boost_source_copy_command="cp -rL dep-source dep-root",
                boost_root_dir="dep-root",
                patch_dependency_step_name="Patch Dependency",
                project_source_dir="mylib-root",
                restore_timestamps_step_title="Restore mylib source file timestamps",
                file_stats_basename=".mylib-file-stats",
                workspace_libs_copy_marker='cp -r "$workspace_root"',
                workspace_libs_copy_dest="vendor/$pkg",
                b2_workflow_action_marker="build-workflow",
                cached_module_libs_path="vendor/mylib",
            ),
        )
    )


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


@pytest.fixture
def patched_workflow(workflow_path: Path, sample_entry: MatrixEntry):
    """Write patched workflow with optional args; yield writer; unlink on teardown."""
    paths: list[Path] = []

    def _write(
        workflow: Path | None = None,
        entry: MatrixEntry | None = None,
        *,
        image_tag: str | None = None,
        job_id: str | None = None,
        container_mount_options: str | None = None,
        config: LocalCIConfig | None = None,
    ) -> Path:
        patched = _write_patched_workflow(
            workflow or workflow_path,
            entry or sample_entry,
            image_tag=image_tag,
            job_id=job_id,
            container_mount_options=container_mount_options,
            config=config,
        )
        paths.append(patched)
        return patched

    yield _write
    for path in paths:
        path.unlink(missing_ok=True)


def test_pipeline_capy_profile_enables_all_steps(patched_workflow) -> None:
    """Capy profile runs the full patch pipeline (Boost.Capy baseline behaviour)."""
    content = patched_workflow(config=_capy_config()).read_text()
    assert "LOCALCI_B2_SOURCE_DIR" in content
    assert "Restore capy source file timestamps" in content
    assert "Skip b2 bootstrap" in content
    assert 'cp -rp "$workspace_root"/capy-root "libs/$module"' in content
    assert 'cp -r "$workspace_root"' not in content


def test_pipeline_generic_default_skips_capy_steps(patched_workflow) -> None:
    """Default generic profile does not apply Capy/B2-specific patches."""
    content = patched_workflow().read_text()
    assert "LOCALCI_B2_SOURCE_DIR" not in content
    assert "Restore capy source file timestamps" not in content
    assert "Skip b2 bootstrap" not in content
    assert "cp -rL boost-source boost-root" in content
    assert 'cp -r "$workspace_root"/capy-root "libs/$module"' in content
    assert 'cp -rp "$workspace_root"/capy-root "libs/$module"' not in content


def test_pipeline_disable_b2_patches(patched_workflow) -> None:
    """Disabling b2-related patches leaves the workflow unchanged for those steps."""
    cfg = LocalCIConfig(
        patches=PatchesConfig(
            profile="capy",
            b2_source_cache=False,
            restore_capy_timestamps=False,
            capy_copy_preservation=False,
            b2_bootstrap_skip=False,
        )
    )
    content = patched_workflow(config=cfg).read_text()
    assert "LOCALCI_B2_SOURCE_DIR" not in content
    assert "Restore capy source file timestamps" not in content
    assert "Skip b2 bootstrap" not in content
    assert "cp -rL boost-source boost-root" in content


def test_pipeline_disable_container_mounts(patched_workflow) -> None:
    mounts = "-v /host/boost:/tmp/localci-cache/boost"
    mount_fragment = "/host/boost:/tmp/localci-cache/boost"

    patched_enabled = patched_workflow(
        job_id="build",
        container_mount_options=mounts,
        config=LocalCIConfig(patches=PatchesConfig(container_mounts=True)),
    )
    assert mount_fragment in patched_enabled.read_text()

    patched_disabled = patched_workflow(
        job_id="build",
        container_mount_options=mounts,
        config=LocalCIConfig(patches=PatchesConfig(container_mounts=False)),
    )
    assert mount_fragment not in patched_disabled.read_text()


def test_pipeline_custom_order() -> None:
    """Custom order lists all enabled steps in the requested sequence."""
    cfg = LocalCIConfig(
        patches=PatchesConfig(
            profile="capy",
            order=[
                "codecov_skip",
                "b2_source_cache",
                "restore_capy_timestamps",
                "capy_copy_preservation",
                "b2_bootstrap_skip",
                "container_mounts",
                "image_substitution",
            ],
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
    monkeypatch.setattr(
        "localci.core.patch_registry.get_patch_step_registry",
        lambda: incomplete,
    )
    with pytest.raises(ValueError, match="b2_source_cache"):
        PatchPipeline.from_config(LocalCIConfig(patches=PatchesConfig(profile="capy")))


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


def test_patch_step_skip_emits_warning(sample_entry: MatrixEntry, caplog) -> None:
    """Enabled patch step with incomplete context logs a skip warning."""
    ctx = PatchContext(
        lines=["jobs:\n", "  build:\n", "    runs-on: ubuntu-latest\n"],
        entry=sample_entry,
        config=LocalCIConfig(),
        job_id=None,
        container_mount_options=None,
    )
    with caplog.at_level(logging.WARNING, logger="localci.core.patch_pipeline"):
        ContainerMountsStep().apply(ctx)

    assert any(
        r.levelno == logging.WARNING
        and "container_mounts" in r.message
        and "job_id and container_mount_options are required" in r.message
        for r in caplog.records
    )


def test_generic_cpp_workflow_patches_with_project_config(
    patched_workflow,
) -> None:
    """Non-Boost workflow patches through configurable project literals."""
    assert GENERIC_CPP_WORKFLOW.is_file()
    content = patched_workflow(
        GENERIC_CPP_WORKFLOW, config=_generic_project_config()
    ).read_text()
    assert "LOCALCI_B2_SOURCE_DIR" in content
    assert "Restore mylib source file timestamps" in content
    assert ".mylib-file-stats" in content
    assert 'cp -rp "$workspace_root"/mylib-root "vendor/$pkg"' in content
    assert "Skip b2 bootstrap" in content
    assert "dep-root" in content
    assert "boost-source" not in content
    assert "capy-root" not in content


class _MarkerPluginStep(PatchStep):
    """Test-only plugin step that prepends a marker comment."""

    @property
    def name(self) -> str:
        return "marker_plugin"

    def apply(self, ctx: PatchContext) -> None:
        ctx.lines.insert(0, "# patched by marker_plugin\n")


def test_custom_plugin_step_via_registry(
    patched_workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry-point-registered steps can be enabled via extra_steps."""
    from localci.core.patch_steps import PATCH_STEP_REGISTRY

    registry = dict(PATCH_STEP_REGISTRY)
    registry["marker_plugin"] = _MarkerPluginStep
    monkeypatch.setattr(
        "localci.core.patch_registry.get_patch_step_registry",
        lambda: registry,
    )

    cfg = LocalCIConfig(
        patches=PatchesConfig(
            container_mounts=False,
            b2_source_cache=False,
            restore_capy_timestamps=False,
            capy_copy_preservation=False,
            b2_bootstrap_skip=False,
            image_substitution=False,
            codecov_skip=False,
            extra_steps={"marker_plugin": True},
            order=["marker_plugin"],
        )
    )
    content = patched_workflow(config=cfg).read_text()
    assert content.startswith("# patched by marker_plugin\n")


def test_custom_plugin_step_runs_without_explicit_order(
    patched_workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled extra_steps append to the default built-in order when order is omitted."""
    from localci.core.patch_steps import PATCH_STEP_REGISTRY

    registry = dict(PATCH_STEP_REGISTRY)
    registry["marker_plugin"] = _MarkerPluginStep
    monkeypatch.setattr(
        "localci.core.patch_registry.get_patch_step_registry",
        lambda: registry,
    )

    cfg = LocalCIConfig(
        patches=PatchesConfig(
            container_mounts=False,
            b2_source_cache=False,
            restore_capy_timestamps=False,
            capy_copy_preservation=False,
            b2_bootstrap_skip=False,
            image_substitution=False,
            codecov_skip=False,
            extra_steps={"marker_plugin": True},
        )
    )
    content = patched_workflow(config=cfg).read_text()
    assert content.startswith("# patched by marker_plugin\n")


def test_extra_steps_rejects_unknown_plugin() -> None:
    with pytest.raises(ValueError, match="Unknown extra patch steps"):
        PatchesConfig(extra_steps={"not_registered": True})


def test_extra_steps_rejects_builtin_duplicate() -> None:
    with pytest.raises(ValueError, match="must not duplicate built-in"):
        PatchesConfig(extra_steps={"codecov_skip": True})
