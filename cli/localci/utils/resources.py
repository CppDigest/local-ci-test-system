"""System resource monitoring for parallel execution.

Uses psutil for CPU/memory/disk when available; falls back to
conservative estimates for container count and Docker CLI.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ResourceSnapshot:
    """Point-in-time system resource snapshot."""

    cpu_percent: float
    memory_percent: float
    memory_available_gb: float
    disk_free_gb: float
    active_containers: int
    timestamp: datetime

    @property
    def is_healthy(self) -> bool:
        """True if resources are within safe limits."""
        return (
            self.cpu_percent < 90.0
            and self.memory_percent < 85.0
            and self.disk_free_gb > 10.0
        )

    def summary(self) -> str:
        return (
            f"CPU: {self.cpu_percent:.0f}% | "
            f"Mem: {self.memory_percent:.0f}% ({self.memory_available_gb:.1f}GB free) | "
            f"Disk: {self.disk_free_gb:.1f}GB free | "
            f"Containers: {self.active_containers}"
        )


class ResourceMonitor:
    """Monitor system resources for safe parallel execution.

    Uses psutil for CPU/memory when available; Docker CLI for container count.
    """

    def __init__(self) -> None:
        self._psutil: Optional[object] = self._try_import_psutil()

    def _try_import_psutil(self) -> Optional[object]:
        try:
            import psutil
            return psutil
        except ImportError:
            logger.warning(
                "psutil not installed. Resource monitoring will use estimates. "
                "Install with: pip install psutil"
            )
            return None

    def snapshot(self) -> ResourceSnapshot:
        cpu = self._get_cpu()
        mem_pct, mem_avail = self._get_memory()
        disk = self._get_disk_free()
        containers = self._get_container_count()
        return ResourceSnapshot(
            cpu_percent=cpu,
            memory_percent=mem_pct,
            memory_available_gb=mem_avail,
            disk_free_gb=disk,
            active_containers=containers,
            timestamp=datetime.now(),
        )

    def check_thresholds(
        self,
        cpu_threshold: float = 90.0,
        memory_threshold: float = 85.0,
        disk_min_gb: float = 10.0,
    ) -> tuple[bool, list[str]]:
        """Check if resources are within thresholds. Returns (ok, warnings)."""
        snap = self.snapshot()
        warnings: list[str] = []
        ok = True
        if snap.cpu_percent > cpu_threshold:
            warnings.append(
                f"CPU at {snap.cpu_percent:.0f}% (threshold: {cpu_threshold}%)"
            )
            ok = False
        if snap.memory_percent > memory_threshold:
            warnings.append(
                f"Memory at {snap.memory_percent:.0f}% "
                f"(threshold: {memory_threshold}%, "
                f"{snap.memory_available_gb:.1f}GB available)"
            )
            ok = False
        if snap.disk_free_gb < disk_min_gb:
            warnings.append(
                f"Disk free: {snap.disk_free_gb:.1f}GB (minimum: {disk_min_gb}GB)"
            )
            ok = False
        return ok, warnings

    def _get_cpu(self) -> float:
        if self._psutil:
            return self._psutil.cpu_percent(interval=0.1)
        return 50.0

    def _get_memory(self) -> tuple[float, float]:
        if self._psutil:
            mem = self._psutil.virtual_memory()
            return mem.percent, mem.available / (1024 ** 3)
        return 50.0, 8.0

    def _get_disk_free(self) -> float:
        if self._psutil:
            if platform.system() == "Windows":
                usage = self._psutil.disk_usage("C:\\")
            else:
                usage = self._psutil.disk_usage("/")
            return usage.free / (1024 ** 3)
        return 50.0

    def _get_container_count(self) -> int:
        try:
            result = subprocess.run(
                ["docker", "ps", "-q"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return len(
                    [l for l in result.stdout.strip().split("\n") if l]
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return 0
