"""Tests for image registry and two-mark matching algorithm."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from localci.core.registry import (
    ImageRegistry,
    MatchResult,
    RegistryEntry,
    essential_marks,
    extra_marks,
    select_image,
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
    compiler_family: CompilerFamily = CompilerFamily.GCC,
    compiler_version: str = "15",
    architecture: str = "x86_64",
    apt_packages: list[str] | None = None,
    build_tools: list[str] | None = None,
) -> MatrixEntry:
    return MatrixEntry(
        index=0,
        name="test",
        platform=Platform.LINUX,
        compiler=CompilerInfo(family=compiler_family, version=compiler_version),
        container=ContainerInfo(image=container_image),
        variant=BuildVariant(),
        packages=PackageRequirements(
            apt_packages=apt_packages or [],
            build_tools=build_tools or [],
        ),
        runs_on="ubuntu-latest",
        build_system=BuildSystem.B2,
        architecture=architecture,
    )


def _make_reg(
    name: str = "img",
    os: str = "ubuntu:25.04",
    architecture: str = "x86_64",
    compilers: list[str] | None = None,
    packages: list[str] | None = None,
    tools: list[str] | None = None,
    usage_count: int = 0,
    last_used: str | None = None,
    size_mb: int | None = None,
) -> RegistryEntry:
    return RegistryEntry(
        name=name,
        file=f"images/{name}.tar",
        docker_tag=f"{name}:latest",
        os=os,
        architecture=architecture,
        compilers=compilers or [],
        packages=packages or [],
        tools=tools or [],
        usage_count=usage_count,
        last_used=last_used,
        size_mb=size_mb,
        variants=[],
    )


class TestEssentialMarks:
    """Essential marks: 0 (mismatch), 70 (OS+arch match), 100 (OS+arch+compiler)."""

    def test_full_match(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg = _make_reg("g15", os="ubuntu:25.04", compilers=["gcc-15"])
        assert essential_marks(entry, reg) == 100

    def test_os_arch_match_compiler_mismatch(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg = _make_reg("g14", os="ubuntu:25.04", compilers=["gcc-14"])
        assert essential_marks(entry, reg) == 70

    def test_os_arch_match_no_compiler_in_registry(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg = _make_reg("base", os="ubuntu:25.04", compilers=[])
        assert essential_marks(entry, reg) == 70

    def test_os_mismatch(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg = _make_reg("u24", os="ubuntu:24.04", compilers=["gcc-15"])
        assert essential_marks(entry, reg) == 0

    def test_arch_mismatch(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15", architecture="x86")
        reg = _make_reg("x64", os="ubuntu:25.04", architecture="x86_64", compilers=["gcc-15"])
        assert essential_marks(entry, reg) == 0

    def test_clang_match(self):
        entry = _make_entry("ubuntu:24.04", CompilerFamily.CLANG, "17")
        reg = _make_reg("c17", os="ubuntu:24.04", compilers=["clang-17"])
        assert essential_marks(entry, reg) == 100


class TestExtraMarks:
    """Extra marks: packages +10, build tools +20."""

    def test_no_requirements(self):
        entry = _make_entry()
        reg = _make_reg(packages=["libssl-dev"], tools=["cmake"])
        assert extra_marks(entry, reg) == 0

    def test_packages_match(self):
        entry = _make_entry(apt_packages=["libssl-dev", "zlib1g-dev"])
        reg = _make_reg(packages=["libssl-dev", "zlib1g-dev"])
        assert extra_marks(entry, reg) == 20  # 2 * 10

    def test_tools_match(self):
        entry = _make_entry(build_tools=["cmake", "lcov"])
        reg = _make_reg(tools=["cmake", "lcov"])
        assert extra_marks(entry, reg) == 40  # 2 * 20

    def test_tool_in_packages_counts_as_package(self):
        entry = _make_entry(build_tools=["cmake"])
        reg = _make_reg(packages=["cmake"])  # no tools
        assert extra_marks(entry, reg) == 10


class TestSelectImage:
    """Selection: full match (essential=100) -> use_image; else -> base_image, needs_build."""

    def test_empty_registry(self):
        entry = _make_entry()
        result = select_image(entry, [])
        assert result.use_image is None
        assert result.needs_build is True
        assert result.base_image is None

    def test_full_match_used(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg = _make_reg("g15", os="ubuntu:25.04", compilers=["gcc-15"])
        result = select_image(entry, [reg])
        assert result.use_image is reg
        assert result.needs_build is False
        assert result.base_image is None
        assert result.essential_marks == 100

    def test_full_match_highest_extra_wins(self):
        entry = _make_entry(apt_packages=["libssl-dev"])
        reg_lo = _make_reg("lo", os="ubuntu:25.04", compilers=["gcc-15"], packages=[])
        reg_hi = _make_reg("hi", os="ubuntu:25.04", compilers=["gcc-15"], packages=["libssl-dev"])
        result = select_image(entry, [reg_lo, reg_hi])
        assert result.use_image is reg_hi
        assert result.extra_marks == 10

    def test_no_full_match_base_selected(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg = _make_reg("base", os="ubuntu:25.04", compilers=[])  # 70
        result = select_image(entry, [reg])
        assert result.use_image is None
        assert result.needs_build is True
        assert result.base_image is reg
        assert result.essential_marks == 70

    def test_tie_break_prefer_recently_used(self):
        entry = _make_entry("ubuntu:25.04", CompilerFamily.GCC, "15")
        reg_old = _make_reg("a", os="ubuntu:25.04", compilers=["gcc-15"], last_used="2020-01-01T00:00:00Z")
        reg_new = _make_reg("b", os="ubuntu:25.04", compilers=["gcc-15"], last_used="2026-01-01T00:00:00Z")
        result = select_image(entry, [reg_old, reg_new])
        assert result.use_image is reg_new


class TestRegistryEntryRoundtrip:
    """RegistryEntry from_dict / to_dict preserves schema."""

    def test_from_dict_minimal(self):
        d = {"name": "x", "file": "x.tar", "docker_tag": "x:latest", "os": "ubuntu:25.04", "architecture": "x86_64"}
        e = RegistryEntry.from_dict(d)
        assert e.name == "x"
        assert e.os == "ubuntu:25.04"
        assert e.compilers == []
        assert e.packages == []

    def test_to_dict_roundtrip(self):
        e = _make_reg("test", compilers=["gcc-15"], packages=["a"], tools=["cmake"])
        d = e.to_dict()
        e2 = RegistryEntry.from_dict(d)
        assert e2.name == e.name
        assert e2.compilers == e.compilers
        assert e2.packages == e.packages
        assert e2.tools == e.tools


class TestImageRegistryCRUD:
    """ImageRegistry load, save, add, update, remove."""

    def test_load_empty_or_missing(self, tmp_path: Path):
        reg = ImageRegistry(tmp_path / "nonexistent.yml")
        reg.load()
        assert reg.entries == []

    def test_load_save_roundtrip(self, tmp_path: Path):
        path = tmp_path / "registry.yml"
        reg = ImageRegistry(path)
        reg.entries = [_make_reg("a"), _make_reg("b")]
        reg.save()
        reg2 = ImageRegistry(path)
        reg2.load()
        assert len(reg2.entries) == 2
        assert reg2.find_by_name("a") is not None
        assert reg2.find_by_name("b").os == "ubuntu:25.04"

    def test_add_remove(self, tmp_path: Path):
        path = tmp_path / "r.yml"
        reg = ImageRegistry(path)
        reg.load()
        reg.add(_make_reg("new"))
        assert reg.find_by_name("new") is not None
        reg.remove("new")
        assert reg.find_by_name("new") is None

    def test_add_duplicate_raises(self, tmp_path: Path):
        path = tmp_path / "r.yml"
        reg = ImageRegistry(path)
        reg.add(_make_reg("x"))
        with pytest.raises(ValueError, match="already has"):
            reg.add(_make_reg("x"))

    def test_update_usage(self, tmp_path: Path):
        path = tmp_path / "r.yml"
        reg = ImageRegistry(path)
        reg.add(_make_reg("u", usage_count=5))
        reg.update_usage("u")
        e = reg.find_by_name("u")
        assert e.usage_count == 6
        assert e.last_used is not None


class TestQueueBuilderWithRegistry:
    """QueueBuilder with registry_path sets image_tag, base_image_tag, needs_build."""

    def test_with_registry_full_match(self, tmp_path: Path):
        from localci.core.queue_builder import QueueBuilder, _resolve_image_tag_and_build
        from localci.core.workflow import WorkflowAnalyzer

        registry_path = tmp_path / "image-registry.yml"
        registry_path.write_text(
            yaml.safe_dump({
                "version": "1.0",
                "images": [
                    {
                        "name": "capy-ubuntu-25.04-gcc15",
                        "docker_tag": "capy-ubuntu-25.04-gcc15:latest",
                        "file": "images/capy-ubuntu-25.04-gcc15.tar",
                        "os": "ubuntu:25.04",
                        "architecture": "x86_64",
                        "compilers": ["gcc-15"],
                    },
                ],
            }),
            encoding="utf-8",
        )
        # Create a minimal workflow with one job/entry
        wf_path = tmp_path / "ci.yml"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(
            """
name: CI
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - compiler: gcc
            version: "15"
            container: ubuntu:25.04
""",
            encoding="utf-8",
        )
        analyzer = WorkflowAnalyzer()
        wf = analyzer.analyze(wf_path)
        builder = QueueBuilder(wf)
        queue = builder.build(registry_path=registry_path)
        jobs = list(queue.get_all_jobs())
        assert len(jobs) == 1
        assert jobs[0].image_tag == "capy-ubuntu-25.04-gcc15:latest"
        assert jobs[0].needs_build is False
        assert jobs[0].base_image_tag is None

    def test_with_registry_no_match_needs_build(self, tmp_path: Path):
        from localci.core.queue_builder import QueueBuilder
        from localci.core.workflow import WorkflowAnalyzer

        registry_path = tmp_path / "image-registry.yml"
        registry_path.write_text(
            yaml.safe_dump({"version": "1.0", "images": []}),
            encoding="utf-8",
        )
        wf_path = tmp_path / "ci.yml"
        wf_path.write_text(
            """
name: CI
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - compiler: gcc
            version: "15"
            container: ubuntu:25.04
""",
            encoding="utf-8",
        )
        analyzer = WorkflowAnalyzer()
        wf = analyzer.analyze(wf_path)
        builder = QueueBuilder(wf)
        queue = builder.build(registry_path=registry_path)
        jobs = list(queue.get_all_jobs())
        assert len(jobs) == 1
        assert jobs[0].needs_build is True
        assert "gcc" in (jobs[0].image_tag or "")
