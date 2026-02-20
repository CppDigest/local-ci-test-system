"""Docker image and container management for Local CI.

Handles loading pre-built images from ``.tar`` files, tagging for
``act``, and container cleanup.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DockerManager:
    """Manage Docker images and containers for local CI.

    Handles loading pre-built images from .tar files, tagging for
    ``act``, and cleanup.

    Usage::

        dm = DockerManager()
        ok, msg = dm.load_image(Path("images/capy-ubuntu-25.04-gcc15.tar"))
        dm.tag_image(msg, "ubuntu-latest:local")
    """

    def __init__(self) -> None:
        self._docker_path: Optional[str] = shutil.which("docker")
        self._check_docker()

    # -----------------------------------------------------------------
    # Preflight
    # -----------------------------------------------------------------

    @property
    def has_docker(self) -> bool:
        """``True`` when the ``docker`` binary is on ``PATH``."""
        return self._docker_path is not None

    def _check_docker(self) -> None:
        """Verify Docker is available."""
        if not self._docker_path:
            raise RuntimeError("Docker is not installed")

        result = subprocess.run(
            [self._docker_path, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("Docker version: %s", result.stdout.strip())
        else:
            raise RuntimeError("Docker daemon not responding")

    # -----------------------------------------------------------------
    # Image operations
    # -----------------------------------------------------------------

    def _docker_cmd(self, *args: str) -> list[str]:
        """Build a Docker command using the resolved binary path."""
        assert self._docker_path is not None
        return [self._docker_path, *args]

    def load_image(self, tar_path: Path) -> tuple[bool, str]:
        """Load Docker image from a ``.tar`` file.

        Returns ``(success, output_or_error)``. On success, output is the raw
        stdout (e.g. "Loaded image: repo:tag" or "Loaded image ID: sha256:...").
        Use parse_load_output() to get an image reference for tagging.
        """
        if not tar_path.exists():
            return False, f"Image file not found: {tar_path}"

        logger.info("Loading image: %s", tar_path)
        start = time.time()

        result = subprocess.run(
            self._docker_cmd("load", "-i", str(tar_path)),
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max for large images
        )

        duration = time.time() - start

        if result.returncode != 0:
            return False, f"Failed to load: {result.stderr.strip()}"

        output = result.stdout.strip()
        logger.info("Loaded in %.1fs: %s", duration, output)
        return True, output

    @staticmethod
    def parse_load_output(load_stdout: str) -> Optional[str]:
        """Parse docker load stdout to get image reference for tagging.

        - "Loaded image: repo:tag" -> "repo:tag"
        - "Loaded image ID: sha256:abc..." -> "sha256:abc..."
        - Multiple lines (e.g. multiple images in tar) -> first image ref
        Returns None if no recognized pattern.
        """
        for line in load_stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("Loaded image: "):
                return line.replace("Loaded image: ", "", 1).strip()
            if line.startswith("Loaded image ID: "):
                return line.replace("Loaded image ID: ", "", 1).strip()
        return None

    def save_image(self, image_ref: str, tar_path: Path) -> tuple[bool, str]:
        """Save a Docker image to a ``.tar`` file.

        image_ref: image name:tag or ID (e.g. capy-ubuntu-25.04-gcc15:latest).
        Returns (success, error_message). On success error_message is empty.
        """
        tar_path = Path(tar_path)
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Saving image %s to %s", image_ref, tar_path)
        start = time.time()
        result = subprocess.run(
            self._docker_cmd("save", "-o", str(tar_path), image_ref),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min for large images
        )
        duration = time.time() - start
        if result.returncode != 0:
            return False, result.stderr.strip() or "docker save failed"
        logger.info("Saved in %.1fs", duration)
        return True, ""

    def tag_image(self, source: str, target: str) -> bool:
        """Tag a Docker image."""
        result = subprocess.run(
            self._docker_cmd("tag", source, target),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0

    def image_exists(self, name: str) -> bool:
        """Check if Docker image exists locally."""
        result = subprocess.run(
            self._docker_cmd("image", "inspect", name),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    def remove_image(self, name: str, force: bool = False) -> bool:
        """Remove a Docker image."""
        args = ["rmi"]
        if force:
            args.append("-f")
        args.append(name)
        result = subprocess.run(
            self._docker_cmd(*args),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0

    def image_size(self, name: str) -> Optional[float]:
        """Get image size in MB, or ``None`` if unavailable."""
        result = subprocess.run(
            self._docker_cmd(
                "image", "inspect", name, "--format", "{{.Size}}"
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            try:
                return int(result.stdout.strip()) / (1024 * 1024)
            except ValueError:
                pass
        return None

    # -----------------------------------------------------------------
    # Container cleanup
    # -----------------------------------------------------------------

    def list_containers(self, label: str = "localci") -> list[str]:
        """List container IDs with a specific label."""
        result = subprocess.run(
            self._docker_cmd(
                "ps", "-a", "--filter", f"label={label}", "-q"
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return [
                c.strip()
                for c in result.stdout.strip().split("\n")
                if c.strip()
            ]
        return []

    def cleanup_act_containers(self) -> int:
        """Remove all containers created by ``act``.

        ``act`` container names typically start with ``act-``.
        Returns the number of containers removed.
        """
        result = subprocess.run(
            self._docker_cmd("ps", "-a", "--filter", "name=act-", "-q"),
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return 0

        container_ids = [
            c.strip()
            for c in result.stdout.strip().split("\n")
            if c.strip()
        ]

        if container_ids:
            subprocess.run(
                self._docker_cmd("rm", "-f", *container_ids),
                capture_output=True,
                text=True,
                timeout=60,
            )

        logger.info("Cleaned up %d act containers", len(container_ids))
        return len(container_ids)

    # -----------------------------------------------------------------
    # Resource monitoring
    # -----------------------------------------------------------------

    def disk_usage(self) -> dict:
        """Get Docker disk usage summary."""
        result = subprocess.run(
            self._docker_cmd(
                "system", "df", "--format", "{{json .}}"
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return {"raw": result.stdout.strip()}
        return {}
