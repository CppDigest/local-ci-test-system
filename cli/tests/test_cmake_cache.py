"""Tests for CMake cache change detection (Issue 11)."""

from __future__ import annotations

from localci.core.cmake_cache import compute_cmake_input_digest
from localci.core.config import CmakeCacheConfig
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


def _make_entry(cc: str = "gcc-15", cxx: str = "g++-15") -> MatrixEntry:
    return MatrixEntry(
        index=0,
        name="GCC 15",
        platform=Platform.LINUX,
        compiler=CompilerInfo(family=CompilerFamily.GCC, version="15", cc=cc, cxx=cxx),
        container=ContainerInfo(),
        variant=BuildVariant(),
        packages=PackageRequirements(),
        runs_on="ubuntu-latest",
        build_system=BuildSystem.CMAKE,
    )


class TestComputeCmakeInputDigest:
    def test_returns_12_char_hex(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        cfg = CmakeCacheConfig()
        entry = _make_entry()
        digest = compute_cmake_input_digest(tmp_path, entry, cfg, boost_enabled=False)
        assert len(digest) == 12
        assert all(c in "0123456789abcdef" for c in digest)

    def test_same_inputs_same_digest(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        cfg = CmakeCacheConfig()
        entry = _make_entry()
        d1 = compute_cmake_input_digest(tmp_path, entry, cfg, boost_enabled=False)
        d2 = compute_cmake_input_digest(tmp_path, entry, cfg, boost_enabled=False)
        assert d1 == d2

    def test_different_compiler_different_digest(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        cfg = CmakeCacheConfig()
        e1 = _make_entry(cc="gcc-15", cxx="g++-15")
        e2 = _make_entry(cc="clang-20", cxx="clang++-20")
        d1 = compute_cmake_input_digest(tmp_path, e1, cfg, boost_enabled=False)
        d2 = compute_cmake_input_digest(tmp_path, e2, cfg, boost_enabled=False)
        assert d1 != d2

    def test_boost_enabled_affects_digest(self, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        cfg = CmakeCacheConfig()
        entry = _make_entry()
        d1 = compute_cmake_input_digest(tmp_path, entry, cfg, boost_enabled=False)
        d2 = compute_cmake_input_digest(tmp_path, entry, cfg, boost_enabled=True)
        assert d1 != d2
