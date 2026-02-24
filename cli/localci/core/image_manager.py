"""Docker image management: load from .tar, create new images, save for reuse.

Implements Issue 4: Docker Image Management per Design Guide.
- Load images from .tar files
- Create new images when no match found (needs_build)
- Save newly built images and update registry
- Image naming convention: <project>-<os>-<variant>
- Integrates with ImageRegistry (Issue 3) and DockerManager.
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from localci.core.registry import (
    ImageRegistry,
    RegistryEntry,
)
from localci.utils.docker import DockerManager

if TYPE_CHECKING:
    from localci.core.models import QueuedJob
    from localci.core.workflow import MatrixEntry

logger = logging.getLogger(__name__)

# Default project name for image naming (e.g. capy)
DEFAULT_PROJECT = "capy"


def image_name_from_entry(entry: "MatrixEntry", project: str = DEFAULT_PROJECT) -> str:
    """Derive image name from matrix entry (no :latest).

    Naming convention: <project>-<os>-<variant> per Design Guide E.4.
    Examples: capy-ubuntu-25.04-gcc15, capy-ubuntu-24.04-clang20-asan.
    """
    if entry.container.image:
        img = entry.container.image.strip().lower()
        os_label = img.replace(":", "-", 1) if ":" in img else img
    else:
        os_label = (entry.runs_on or "ubuntu-latest").strip().lower()
    compiler_label = f"{entry.compiler.family.value}{entry.compiler.version}"
    base = f"{project}-{os_label}-{compiler_label}"
    if entry.variant.coverage:
        base += "-cov"
    elif entry.variant.asan:
        base += "-asan"
    elif entry.variant.x86:
        base += "-x86"
    return base


def image_name_base_from_entry(entry: "MatrixEntry", project: str = DEFAULT_PROJECT) -> str:
    """Derive base-only image name (OS + compiler, no variant suffix).

    Use when building a single base image per OS+toolchain for all variants
    (standard, asan, x86, cov); variants use the same image with different flags.
    """
    if entry.container.image:
        img = entry.container.image.strip().lower()
        os_label = img.replace(":", "-", 1) if ":" in img else img
    else:
        os_label = (entry.runs_on or "ubuntu-latest").strip().lower()
    compiler_label = f"{entry.compiler.family.value}{entry.compiler.version}"
    return f"{project}-{os_label}-{compiler_label}"


def _sanitize_name_for_dockerfile(name: str) -> str:
    """Dockerfile filenames: allow alphanumeric, dash, dot."""
    return re.sub(r"[^a-zA-Z0-9.-]", "-", name)


class ImageManager:
    """Prepare Docker images for jobs: load from .tar, build when no match, save for reuse."""

    def __init__(
        self,
        project_dir: Path,
        registry_path: Path,
        images_dir: Optional[Path] = None,
        project: str = DEFAULT_PROJECT,
        docker: Optional[DockerManager] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.registry_path = Path(registry_path)
        self.images_dir = Path(images_dir) if images_dir else (self.project_dir / "images" / project)
        self.project = project
        self._docker = docker or DockerManager()

    def _registry(self) -> ImageRegistry:
        reg = ImageRegistry(self.registry_path)
        reg.load()
        return reg

    def _resolve_tar_path(self, file_path: str) -> Path:
        """Resolve registry 'file' (relative or absolute) to Path."""
        p = Path(file_path)
        if not p.is_absolute():
            p = self.project_dir / p
        return p.resolve()

    def _load_from_tar(self, entry: RegistryEntry, target_tag: Optional[str] = None) -> bool:
        """Load image from registry entry's .tar file; optionally tag as target_tag."""
        tar_path = self._resolve_tar_path(entry.file)
        ok, output = self._docker.load_image(tar_path)
        if not ok:
            logger.error("Load failed: %s", output)
            return False
        ref = DockerManager.parse_load_output(output)
        if ref and target_tag and ref != target_tag:
            if ref.startswith("sha256:"):
                self._docker.tag_image(ref, target_tag)
            elif ref != target_tag:
                self._docker.tag_image(ref, target_tag)
        return True

    def prepare_image_for_job(self, job: "QueuedJob") -> Optional[str]:
        """Ensure the image for this job is available; load or build as needed.

        Returns the image tag to use for act (e.g. capy-ubuntu-25.04-gcc15:latest),
        or None on failure.
        """
        registry = self._registry()
        if job.needs_build:
            base_entry = None
            if job.base_image_tag:
                for e in registry.entries:
                    if e.docker_tag == job.base_image_tag:
                        base_entry = e
                        break
            return self._build_new_image(job, registry, base_entry)

        # Use existing image: find registry entry by docker_tag and load from .tar if needed
        entry = None
        for e in registry.entries:
            if e.docker_tag == job.image_tag:
                entry = e
                break
        if not entry:
            logger.warning("Image tag %s not in registry; assuming pre-loaded", job.image_tag)
            if self._docker.image_exists(job.image_tag or ""):
                return job.image_tag
            return None
        if self._docker.image_exists(job.image_tag or ""):
            registry.update_usage(entry.name)
            try:
                registry.save()
            except Exception as e:
                logger.debug("Could not save registry usage: %s", e)
            return job.image_tag
        if not self._load_from_tar(entry, job.image_tag):
            return None
        registry.update_usage(entry.name)
        try:
            registry.save()
        except Exception as e:
            logger.debug("Could not save registry: %s", e)
        return job.image_tag

    def _build_new_image(
        self,
        job: "QueuedJob",
        registry: ImageRegistry,
        base_entry: Optional[RegistryEntry],
    ) -> Optional[str]:
        """Build a new image when no full match exists; save to .tar and add to registry.

        Uses base-only naming (OS + compiler, no variant) so one image serves
        all variants (standard, asan, x86, cov); variants use workflow flags.
        """
        entry = job.matrix_entry
        image_name = image_name_base_from_entry(entry, self.project)
        image_tag = f"{image_name}:latest"

        if self._docker.image_exists(image_tag):
            logger.debug("Base image already exists: %s", image_tag)
            return image_tag

        # Ensure base image is loaded
        if base_entry:
            if not self._docker.image_exists(base_entry.docker_tag):
                if not self._load_from_tar(base_entry):
                    logger.error("Failed to load base image %s", base_entry.docker_tag)
                    return None
            base_from = base_entry.docker_tag
        else:
            base_from = None  # Will use official base from entry (e.g. ubuntu:25.04)

        dockerfile_path = self._find_dockerfile(image_name)
        if dockerfile_path and dockerfile_path.exists():
            ok = self._build_with_dockerfile(dockerfile_path, image_tag)
        else:
            ok = self._build_from_generated(entry, image_tag, base_from)

        if not ok:
            return None

        # Save to .tar and add to registry
        tar_rel = f"images/{self.project}/{image_name}.tar"
        tar_path = self.project_dir / tar_rel
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        ok_save, err = self._docker.save_image(image_tag, tar_path)
        if not ok_save:
            logger.error("Failed to save image: %s", err)
            return image_tag  # Image exists in Docker; still usable

        try:
            file_rel = str(tar_path.relative_to(self.project_dir))
        except ValueError:
            file_rel = str(tar_path)
        new_entry = self._registry_entry_for_built(entry, image_name, file_rel)
        existing = registry.find_by_name(new_entry.name)
        if existing:
            registry.remove(new_entry.name)
        registry.add(new_entry)
        registry.save()
        return image_tag

    def _find_dockerfile(self, image_name: str) -> Optional[Path]:
        """Look for Dockerfile.<image_name> or Dockerfile.<sanitized> in images_dir."""
        safe = _sanitize_name_for_dockerfile(image_name)
        candidates = [
            self.images_dir / f"Dockerfile.{image_name}",
            self.images_dir / f"Dockerfile.{safe}",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _build_with_dockerfile(self, dockerfile_path: Path, image_tag: str) -> bool:
        """Run docker build -f <dockerfile> -t <tag> <context_dir>."""
        import subprocess
        context = dockerfile_path.parent
        cmd = [
            "docker", "build",
            "-f", str(dockerfile_path),
            "-t", image_tag,
            str(context),
        ]
        logger.info("Building image with Dockerfile: %s", dockerfile_path.name)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            logger.error("Build failed: %s", result.stderr)
            return False
        return True

    def _build_from_generated(
        self,
        entry: "MatrixEntry",
        image_tag: str,
        base_from: Optional[str],
    ) -> bool:
        """Generate a minimal Dockerfile and build (FROM base, install compiler + packages)."""
        if not base_from:
            # Use container image from matrix as base
            base_from = (entry.container.image or "ubuntu:24.04").strip()
        req_os = entry.container.image or "ubuntu:24.04"
        compiler_family = entry.compiler.family.value
        compiler_ver = entry.compiler.version or "13"
        apt_packages = list(getattr(entry.packages, "apt_packages", None) or [])
        build_tools = list(getattr(entry.packages, "build_tools", None) or [])
        all_pkgs = list(dict.fromkeys(apt_packages + build_tools))
        # Add compiler packages
        if compiler_family == "gcc":
            all_pkgs.append(f"gcc-{compiler_ver}")
            all_pkgs.append(f"g++-{compiler_ver}")
        elif compiler_family == "clang":
            all_pkgs.append(f"clang-{compiler_ver}")
            all_pkgs.append(f"clang-tools-{compiler_ver}")
        all_pkgs = [p for p in all_pkgs if p]
        run_apt = (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            + " ".join(all_pkgs)
            + " && rm -rf /var/lib/apt/lists/*"
        ) if all_pkgs else ""
        set_env = ""
        if compiler_family == "gcc":
            set_env = f'ENV CC=gcc-{compiler_ver} CXX=g++-{compiler_ver}'
        elif compiler_family == "clang":
            set_env = f'ENV CC=clang-{compiler_ver} CXX=clang++-{compiler_ver}'
        lines = [
            f"FROM {base_from}",
            "RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*",
        ]
        if run_apt:
            lines.append(run_apt)
        if set_env:
            lines.append(set_env)
        content = "\n".join(lines)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".Dockerfile", delete=False) as f:
            f.write(content)
            df_path = Path(f.name)
        try:
            import subprocess
            # Use images_dir as context (no COPY in generated Dockerfile)
            context = str(self.images_dir) if self.images_dir.exists() else str(df_path.parent)
            cmd = ["docker", "build", "-f", str(df_path), "-t", image_tag, context]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                logger.error("Generated build failed: %s", result.stderr)
                return False
            return True
        finally:
            df_path.unlink(missing_ok=True)

    def _registry_entry_for_built(
        self,
        entry: "MatrixEntry",
        image_name: str,
        file_rel: str,
    ) -> RegistryEntry:
        """Build a RegistryEntry for a newly built image."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        size_mb = None
        if self._docker.image_exists(f"{image_name}:latest"):
            size_mb = int(self._docker.image_size(f"{image_name}:latest") or 0)
        req_os = (entry.container.image or "ubuntu:24.04").strip().lower()
        arch = getattr(entry, "architecture", "x86_64") or "x86_64"
        compilers = [f"{entry.compiler.family.value}-{entry.compiler.version}"]
        apt_packages = list(getattr(entry.packages, "apt_packages", None) or [])
        tools = list(getattr(entry.packages, "build_tools", None) or [])
        return RegistryEntry(
            name=image_name,
            file=file_rel,
            docker_tag=f"{image_name}:latest",
            os=req_os,
            architecture=arch,
            packages=apt_packages,
            compilers=compilers,
            tools=tools,
            size_mb=size_mb,
            created=now,
            last_used=now,
            usage_count=0,
        )

    def save_image_to_tar(
        self,
        image_tag: str,
        tar_path: Path,
        registry: Optional[ImageRegistry] = None,
        entry: Optional[RegistryEntry] = None,
    ) -> bool:
        """Save a Docker image to .tar; optionally add/update registry."""
        ok, err = self._docker.save_image(image_tag, Path(tar_path))
        if not ok:
            logger.error("Save failed: %s", err)
            return False
        if registry and entry:
            try:
                existing = registry.find_by_name(entry.name)
                if existing:
                    registry.update(entry.name, file=str(tar_path), last_used=entry.last_used)
                else:
                    registry.add(entry)
                registry.save()
            except Exception as e:
                logger.warning("Could not update registry: %s", e)
        return True
