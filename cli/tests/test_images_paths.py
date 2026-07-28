"""Tests for image registry and images/capy path resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
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
    (images_dir / "build-one.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return images_dir


def _write_registry_with_image(
    directory: Path,
    *,
    name: str = "test-image",
    docker_tag: str = "test:latest",
    created: str = "2026-02-10T00:00:00Z",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / REGISTRY_FILENAME
    path.write_text(
        "version: '1.0'\n"
        "images:\n"
        f"  - name: {name}\n"
        "    file: images/test.tar\n"
        f"    docker_tag: {docker_tag}\n"
        "    os: ubuntu:24.04\n"
        "    architecture: x86_64\n"
        f"    created: {created}\n",
        encoding="utf-8",
    )
    return path


runner = CliRunner()


class TestResolveRegistryPath:
    def test_explicit_registry_path(self, tmp_path: Path) -> None:
        registry = _write_registry(tmp_path)
        assert resolve_registry_path(registry) == registry.resolve()

    def test_find_from_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        assert resolve_registry_path(module_file=module) == registry.resolve()

    def test_simulated_site_packages_install_uses_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        cwd_registry = _write_registry_with_image(repo, name="cwd-registry-image")
        site_packages_root = tmp_path / "venv" / "lib" / "python3.10" / "site-packages"
        site_packages = site_packages_root / "localci" / "cli"
        site_packages.mkdir(parents=True, exist_ok=True)
        _write_registry_with_image(site_packages_root, name="module-registry-image")
        module = site_packages / "images.py"
        module.write_text("# installed copy\n", encoding="utf-8")
        monkeypatch.chdir(repo)

        assert resolve_registry_path(module_file=module) == cwd_registry.resolve()

    def test_missing_registry_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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


class TestImagesRegistryCli:
    def test_list_discovers_registry_from_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        nested = repo / "subdir"
        nested.mkdir(parents=True)
        _write_registry_with_image(repo)
        monkeypatch.chdir(nested)

        result = runner.invoke(cli, ["images", "list", "--format", "json"])

        assert result.exit_code == 0
        assert "test-image" in result.output

    def test_build_without_targets_skips_registry_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        with patch("localci.cli.images._resolve_registry_file") as mock_resolve:
            result = runner.invoke(cli, ["images", "build"])

        mock_resolve.assert_not_called()
        assert result.exit_code == 0

    def test_list_uses_explicit_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _write_registry_with_image(tmp_path / "repo")
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        result = runner.invoke(
            cli, ["images", "list", "--registry", str(registry), "--format", "json"]
        )

        assert result.exit_code == 0
        assert "test-image" in result.output
        assert "2026-02-10T00:00:00Z" in result.output

    def test_info_uses_explicit_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry_with_image(repo)
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        result = runner.invoke(
            cli,
            ["images", "info", "test-image", "--registry", str(registry)],
        )

        assert result.exit_code == 0
        assert "test-image" in result.output

    def test_build_invokes_scripts_beside_explicit_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry(repo)
        images_dir = _write_images_capy(repo)
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured: list[list[str]] = []

        def fake_run(cmd: list[str]) -> MagicMock:
            captured.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("localci.cli.images._run", side_effect=fake_run):
            result = runner.invoke(
                cli,
                ["images", "build", "test-image", "--registry", str(registry)],
            )

        assert result.exit_code == 0
        assert captured[0][1] == str(images_dir / "build-one.sh")

    def test_build_site_packages_layout_uses_registry_scripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "project"
        site_module = (
            project
            / "venv"
            / "lib"
            / "python3.10"
            / "site-packages"
            / "localci"
            / "cli"
            / "images.py"
        )
        site_module.parent.mkdir(parents=True, exist_ok=True)
        site_module.write_text("# installed copy\n", encoding="utf-8")
        _write_registry(project)
        images_dir = _write_images_capy(project)
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured: list[list[str]] = []

        def fake_run(cmd: list[str]) -> MagicMock:
            captured.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("localci.cli.images._IMAGES_MODULE", site_module),
            patch("localci.cli.images._run", side_effect=fake_run),
        ):
            result = runner.invoke(cli, ["images", "build", "--all"])

        assert result.exit_code == 0
        assert captured[0][1] == str(images_dir / "build-all.sh")
