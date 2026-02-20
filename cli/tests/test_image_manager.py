"""Tests for Docker image management (Issue 4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localci.core.image_manager import (
    ImageManager,
    image_name_from_entry,
)
from localci.core.workflow import (
    BuildVariant,
    CompilerFamily,
    CompilerInfo,
    ContainerInfo,
    MatrixEntry,
    PackageRequirements,
    Platform,
    BuildSystem,
)


def _make_entry(
    container_image: str = "ubuntu:25.04",
    compiler_family: str = "gcc",
    compiler_version: str = "15",
    coverage: bool = False,
    asan: bool = False,
    x86: bool = False,
) -> MatrixEntry:
    family = CompilerFamily.GCC if compiler_family == "gcc" else CompilerFamily.CLANG
    return MatrixEntry(
        index=0,
        name="GCC 15",
        platform=Platform.LINUX,
        compiler=CompilerInfo(
            family=family,
            version=compiler_version,
        ),
        container=ContainerInfo(image=container_image),
        variant=BuildVariant(coverage=coverage, asan=asan, x86=x86),
        packages=PackageRequirements(),
        runs_on="ubuntu-latest",
        build_system=BuildSystem.CMAKE,
        architecture="x86_64",
    )


class TestImageNameFromEntry:
    """image_name_from_entry naming convention."""

    def test_ubuntu_gcc(self):
        entry = _make_entry(container_image="ubuntu:25.04", compiler_family="gcc", compiler_version="15")
        assert image_name_from_entry(entry) == "capy-ubuntu-25.04-gcc15"
        assert image_name_from_entry(entry, project="beast2") == "beast2-ubuntu-25.04-gcc15"

    def test_ubuntu_clang(self):
        entry = _make_entry(container_image="ubuntu:24.04", compiler_family="clang", compiler_version="20")
        assert image_name_from_entry(entry) == "capy-ubuntu-24.04-clang20"

    def test_variant_coverage(self):
        entry = _make_entry(coverage=True)
        assert image_name_from_entry(entry) == "capy-ubuntu-25.04-gcc15-cov"

    def test_variant_asan(self):
        entry = _make_entry(asan=True)
        assert image_name_from_entry(entry) == "capy-ubuntu-25.04-gcc15-asan"

    def test_variant_x86(self):
        entry = _make_entry(x86=True)
        assert image_name_from_entry(entry) == "capy-ubuntu-25.04-gcc15-x86"


class TestImageManager:
    """ImageManager with mocked Docker."""

    @patch("localci.core.image_manager.DockerManager")
    def test_prepare_image_use_existing_loaded(self, mock_docker_cls, tmp_path):
        registry_path = tmp_path / "image-registry.yml"
        registry_path.write_text("""
version: "1.0"
images:
  - name: capy-ubuntu-25.04-gcc15
    file: images/capy/capy-ubuntu-25.04-gcc15.tar
    docker_tag: capy-ubuntu-25.04-gcc15:latest
    os: ubuntu:25.04
    architecture: x86_64
    compilers: [gcc-15]
""")
        mock_docker = MagicMock()
        mock_docker.image_exists.return_value = True
        mock_docker_cls.return_value = mock_docker

        from localci.core.models import QueuedJob

        entry = _make_entry()
        job = QueuedJob(
            job_id="build",
            matrix_entry=entry,
            priority=1,
            dependencies=[],
            image_tag="capy-ubuntu-25.04-gcc15:latest",
            base_image_tag=None,
            needs_build=False,
        )
        mgr = ImageManager(
            project_dir=tmp_path,
            registry_path=registry_path,
            docker=mock_docker,
        )
        tag = mgr.prepare_image_for_job(job)
        assert tag == "capy-ubuntu-25.04-gcc15:latest"
        mock_docker.load_image.assert_not_called()

    @patch("localci.core.image_manager.DockerManager")
    def test_prepare_image_load_from_tar(self, mock_docker_cls, tmp_path):
        registry_path = tmp_path / "image-registry.yml"
        registry_path.write_text("""
version: "1.0"
images:
  - name: capy-ubuntu-25.04-gcc15
    file: images/capy/capy-ubuntu-25.04-gcc15.tar
    docker_tag: capy-ubuntu-25.04-gcc15:latest
    os: ubuntu:25.04
    architecture: x86_64
""")
        tar_path = tmp_path / "images" / "capy" / "capy-ubuntu-25.04-gcc15.tar"
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        tar_path.write_bytes(b"fake")

        mock_docker = MagicMock()
        mock_docker.image_exists.return_value = False
        mock_docker.load_image.return_value = (True, "Loaded image: capy-ubuntu-25.04-gcc15:latest")
        mock_docker_cls.return_value = mock_docker

        from localci.core.models import QueuedJob

        entry = _make_entry()
        job = QueuedJob(
            job_id="build",
            matrix_entry=entry,
            priority=1,
            dependencies=[],
            image_tag="capy-ubuntu-25.04-gcc15:latest",
            base_image_tag=None,
            needs_build=False,
        )
        mgr = ImageManager(
            project_dir=tmp_path,
            registry_path=registry_path,
            docker=mock_docker,
        )
        tag = mgr.prepare_image_for_job(job)
        assert tag == "capy-ubuntu-25.04-gcc15:latest"
        mock_docker.load_image.assert_called_once()
        call_path = mock_docker.load_image.call_args[0][0]
        assert str(call_path).endswith("capy-ubuntu-25.04-gcc15.tar")
