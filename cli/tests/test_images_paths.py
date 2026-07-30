"""Tests for image registry and images/capy path resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from localci.cli.images import _cleanup_targets
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


def _docker_run_side_effect(
    installed_tags: list[str],
    *,
    captured_rmi: list[list[str]] | None = None,
):
    """Stub _run for clean tests: fake image ls output, record rmi argv."""

    def fake_run(cmd: list[str]) -> MagicMock:
        if "image" in cmd and "ls" in cmd:
            stdout = "\n".join(installed_tags)
            if stdout:
                stdout += "\n"
            return MagicMock(returncode=0, stdout=stdout, stderr="")
        if "rmi" in cmd:
            if captured_rmi is not None:
                captured_rmi.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return fake_run


class TestCleanupTargets:
    def test_intersection_sorted(self) -> None:
        installed = ["b:tag", "a:tag", "other:tag"]
        registry = {"a:tag", "b:tag", "registry-only:tag"}
        assert _cleanup_targets(installed, registry) == ["a:tag", "b:tag"]

    def test_empty_when_no_match(self) -> None:
        assert _cleanup_targets(["other:tag"], {"ubuntu-latest-gcc15:latest"}) == []


class TestImagesClean:
    def test_clean_generic_tag_removes_registry_match_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry_with_image(
            repo, docker_tag="ubuntu-latest-gcc15:latest"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured_rmi: list[list[str]] = []
        fake_dm = MagicMock()
        fake_dm.build_cmd.side_effect = lambda *args: ["docker", *args]

        with (
            patch("localci.cli.images.DockerManager", return_value=fake_dm),
            patch(
                "localci.cli.images._run",
                side_effect=_docker_run_side_effect(
                    ["ubuntu-latest-gcc15:latest", "other:tag"],
                    captured_rmi=captured_rmi,
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                ["images", "clean", "--all", "--registry", str(registry)],
            )

        assert result.exit_code == 0
        assert len(captured_rmi) == 1
        assert captured_rmi[0][-1] == "ubuntu-latest-gcc15:latest"
        assert "other:tag" not in captured_rmi[0]

    def test_clean_capy_tag_regression(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry_with_image(
            repo, docker_tag="capy-ubuntu-25.04-gcc15:latest"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured_rmi: list[list[str]] = []
        fake_dm = MagicMock()
        fake_dm.build_cmd.side_effect = lambda *args: ["docker", *args]

        with (
            patch("localci.cli.images.DockerManager", return_value=fake_dm),
            patch(
                "localci.cli.images._run",
                side_effect=_docker_run_side_effect(
                    [
                        "capy-ubuntu-25.04-gcc15:latest",
                        "capy-ubuntu-orphan:latest",
                        "unrelated:tag",
                    ],
                    captured_rmi=captured_rmi,
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                ["images", "clean", "--all", "--registry", str(registry)],
            )

        assert result.exit_code == 0
        assert len(captured_rmi) == 1
        assert captured_rmi[0][-1] == "capy-ubuntu-25.04-gcc15:latest"
        assert "capy-ubuntu-orphan:latest" not in captured_rmi[0]
        assert "unrelated:tag" not in captured_rmi[0]

    def test_clean_dry_run_lists_targets_without_rmi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry_with_image(
            repo, docker_tag="ubuntu-latest-gcc15:latest"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured_rmi: list[list[str]] = []
        fake_dm = MagicMock()
        fake_dm.build_cmd.side_effect = lambda *args: ["docker", *args]

        with (
            patch("localci.cli.images.DockerManager", return_value=fake_dm),
            patch(
                "localci.cli.images._run",
                side_effect=_docker_run_side_effect(
                    ["ubuntu-latest-gcc15:latest", "other:tag"],
                    captured_rmi=captured_rmi,
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "images",
                    "clean",
                    "--all",
                    "--dry-run",
                    "--registry",
                    str(registry),
                ],
            )

        assert result.exit_code == 0
        assert "ubuntu-latest-gcc15:latest" in result.output
        assert "other:tag" not in result.output
        assert "Dry-run" in result.output
        assert captured_rmi == []

    def test_clean_without_all_does_not_invoke_docker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        with patch("localci.cli.images._run") as mock_run:
            result = runner.invoke(cli, ["images", "clean"])

        mock_run.assert_not_called()
        assert result.exit_code == 0
        assert "Nothing to clean" in result.output

    def test_clean_empty_intersection_reports_no_images(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry_with_image(
            repo, docker_tag="ubuntu-latest-gcc15:latest"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured_rmi: list[list[str]] = []
        fake_dm = MagicMock()
        fake_dm.build_cmd.side_effect = lambda *args: ["docker", *args]

        with (
            patch("localci.cli.images.DockerManager", return_value=fake_dm),
            patch(
                "localci.cli.images._run",
                side_effect=_docker_run_side_effect(
                    ["other:tag"],
                    captured_rmi=captured_rmi,
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                ["images", "clean", "--all", "--registry", str(registry)],
            )

        assert result.exit_code == 0
        assert "No localci images found" in result.output
        assert captured_rmi == []

    def test_clean_uses_explicit_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        registry = _write_registry_with_image(repo, docker_tag="explicit:tag")
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        captured_rmi: list[list[str]] = []
        fake_dm = MagicMock()
        fake_dm.build_cmd.side_effect = lambda *args: ["docker", *args]

        with (
            patch("localci.cli.images.DockerManager", return_value=fake_dm),
            patch(
                "localci.cli.images._run",
                side_effect=_docker_run_side_effect(
                    ["explicit:tag"],
                    captured_rmi=captured_rmi,
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                ["images", "clean", "--all", "--registry", str(registry)],
            )

        assert result.exit_code == 0
        assert captured_rmi[0][-1] == "explicit:tag"

    def test_clean_discovers_registry_from_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        nested = repo / "examples" / "validation-project"
        nested.mkdir(parents=True)
        _write_registry_with_image(repo, docker_tag="ubuntu-latest-gcc15:latest")
        monkeypatch.chdir(nested)

        captured_rmi: list[list[str]] = []
        fake_dm = MagicMock()
        fake_dm.build_cmd.side_effect = lambda *args: ["docker", *args]

        with (
            patch("localci.cli.images.DockerManager", return_value=fake_dm),
            patch(
                "localci.cli.images._run",
                side_effect=_docker_run_side_effect(
                    ["ubuntu-latest-gcc15:latest", "other:tag"],
                    captured_rmi=captured_rmi,
                ),
            ),
        ):
            result = runner.invoke(cli, ["images", "clean", "--all"])

        assert result.exit_code == 0
        assert len(captured_rmi) == 1
        assert captured_rmi[0][-1] == "ubuntu-latest-gcc15:latest"
        assert "other:tag" not in captured_rmi[0]

    def test_clean_all_without_discoverable_registry_fails_before_docker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        site_module = (
            tmp_path
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
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.chdir(outside)

        with (
            patch("localci.cli.images._IMAGES_MODULE", site_module),
            patch("localci.cli.images.DockerManager") as mock_dm,
            patch("localci.cli.images._run") as mock_run,
        ):
            result = runner.invoke(cli, ["images", "clean", "--all"])

        assert result.exit_code == 1
        assert REGISTRY_FILENAME in result.output
        mock_dm.assert_not_called()
        mock_run.assert_not_called()
