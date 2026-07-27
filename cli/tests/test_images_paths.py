"""Tests for image registry and images/capy path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from localci.utils.paths import (
    IMAGES_CAPY_REL,
    REGISTRY_FILENAME,
    resolve_images_dir,
    resolve_registry_path,
)


def _write_registry(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / REGISTRY_FILENAME
    path.write_text("version: '1.0'\nimages: []\n", encoding="utf-8")
    return path


def _write_images_capy(repo_root: Path) -> Path:
    images_dir = repo_root / IMAGES_CAPY_REL
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "build-all.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return images_dir


class TestResolveRegistryPath:
    def test_explicit_registry_path(self, tmp_path: Path) -> None:
        registry = _write_registry(tmp_path)
        assert resolve_registry_path(registry) == registry.resolve()

    def test_find_from_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        nested = repo / "examples" / "validation-project"
        nested.mkdir(parents=True)
        registry = _write_registry(repo)
        monkeypatch.chdir(nested)

        assert resolve_registry_path() == registry.resolve()

    def test_find_from_module_file_when_cwd_has_no_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "local-ci-test-system"
        module = repo / "cli" / "localci" / "cli" / "images.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("# stub\n", encoding="utf-8")
        registry = _write_registry(repo)
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        assert (
            resolve_registry_path(module_file=module) == registry.resolve()
        )

    def test_simulated_site_packages_install_uses_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        registry = _write_registry(repo)
        site_packages = (
            tmp_path
            / "venv"
            / "lib"
            / "python3.10"
            / "site-packages"
            / "localci"
            / "cli"
        )
        site_packages.mkdir(parents=True, exist_ok=True)
        module = site_packages / "images.py"
        module.write_text("# installed copy\n", encoding="utf-8")
        monkeypatch.chdir(repo)

        assert (
            resolve_registry_path(module_file=module) == registry.resolve()
        )

    def test_missing_registry_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        module = tmp_path / "site-packages" / "localci" / "cli" / "images.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("# stub\n", encoding="utf-8")
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)

        with pytest.raises(FileNotFoundError, match=REGISTRY_FILENAME):
            resolve_registry_path(module_file=module)


class TestResolveImagesDir:
    def test_images_dir_beside_registry(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        registry = _write_registry(repo)
        images_dir = _write_images_capy(repo)

        assert resolve_images_dir(registry) == images_dir.resolve()
