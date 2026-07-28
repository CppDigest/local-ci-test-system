"""Tests for derive_image_tag()."""

from __future__ import annotations

from localci.core.config import CAPY_NATIVE_IMAGE_PREFIX, GENERIC_NATIVE_IMAGE_PREFIX
from localci.core.image_tag import derive_image_tag
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


def _entry(
    *,
    runs_on: str = "ubuntu-latest",
    container_image: str | None = "ubuntu:25.04",
    compiler_family: CompilerFamily = CompilerFamily.GCC,
    compiler_version: str = "15",
) -> MatrixEntry:
    return MatrixEntry(
        index=0,
        name="test",
        platform=Platform.LINUX,
        compiler=CompilerInfo(family=compiler_family, version=compiler_version),
        container=ContainerInfo(image=container_image),
        variant=BuildVariant(),
        packages=PackageRequirements(),
        runs_on=runs_on,
        build_system=BuildSystem.B2,
    )


class TestDeriveImageTag:
    def test_generic_profile_no_prefix(self):
        tag = derive_image_tag(_entry(container_image=None))
        assert tag == "ubuntu-latest-gcc15:latest"

    def test_capy_profile_prefix(self):
        tag = derive_image_tag(
            _entry(container_image=None),
            native_image_prefix=CAPY_NATIVE_IMAGE_PREFIX,
        )
        assert tag == "capy-ubuntu-latest-gcc15:latest"

    def test_explicit_custom_prefix(self):
        tag = derive_image_tag(
            _entry(container_image=None),
            native_image_prefix="myproj-",
        )
        assert tag == "myproj-ubuntu-latest-gcc15:latest"

    def test_container_image_os_label(self):
        tag = derive_image_tag(
            _entry(container_image="ubuntu:25.04"),
            native_image_prefix=GENERIC_NATIVE_IMAGE_PREFIX,
        )
        assert tag == "ubuntu-25.04-gcc15:latest"

    def test_container_image_with_capy_prefix(self):
        tag = derive_image_tag(
            _entry(container_image="ubuntu:25.04"),
            native_image_prefix=CAPY_NATIVE_IMAGE_PREFIX,
        )
        assert tag == "capy-ubuntu-25.04-gcc15:latest"

    def test_non_linux_runner_returns_none(self):
        tag = derive_image_tag(_entry(runs_on="windows-latest", container_image=None))
        assert tag is None

    def test_variant_suffixes(self):
        entry = _entry(container_image=None)
        entry.variant.coverage = True
        assert derive_image_tag(entry) == "ubuntu-latest-gcc15-cov:latest"
        assert (
            derive_image_tag(entry, native_image_prefix=CAPY_NATIVE_IMAGE_PREFIX)
            == "capy-ubuntu-latest-gcc15-cov:latest"
        )

        entry.variant.coverage = False
        entry.variant.asan = True
        assert derive_image_tag(entry) == "ubuntu-latest-gcc15-asan:latest"

        entry.variant.asan = False
        entry.variant.x86 = True
        assert derive_image_tag(entry) == "ubuntu-latest-gcc15-x86:latest"
