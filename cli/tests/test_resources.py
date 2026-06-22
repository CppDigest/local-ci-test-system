"""Tests for ResourceMonitor (Issue 4)."""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from localci.utils.resources import ResourceMonitor


class TestResourceMonitor:
    @patch("localci.utils.resources.subprocess.run")
    @patch("localci.utils.resources.ResourceMonitor._try_import_psutil")
    def test_snapshot_with_psutil(self, mock_import, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="")
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 42.0
        mem = MagicMock()
        mem.percent = 60.0
        mem.available = 4 * 1024**3
        mock_psutil.virtual_memory.return_value = mem
        disk = MagicMock()
        disk.free = 100 * 1024**3
        mock_psutil.disk_usage.return_value = disk
        mock_import.return_value = mock_psutil

        monitor = ResourceMonitor()
        snap = monitor.snapshot()

        assert snap.cpu_percent == 42.0
        assert snap.memory_percent == 60.0
        assert snap.memory_available_gb == pytest.approx(4.0)
        assert snap.disk_free_gb == pytest.approx(100.0)

    @patch("localci.utils.resources.subprocess.run")
    @patch("localci.utils.resources.ResourceMonitor._try_import_psutil")
    def test_snapshot_without_psutil_uses_estimates(self, mock_import, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="")
        mock_import.return_value = None

        monitor = ResourceMonitor()
        snap = monitor.snapshot()

        assert snap.cpu_percent == 50.0
        assert snap.memory_percent == 50.0
        assert snap.disk_free_gb == 50.0

    def test_import_error_emits_warning(self, caplog):
        with (
            patch.dict(sys.modules, {"psutil": None}),
            caplog.at_level(logging.WARNING, logger="localci.utils.resources"),
        ):
            monitor = ResourceMonitor()

        assert "psutil" in caplog.text
        assert monitor._psutil is None

    @patch("localci.utils.resources.subprocess.run")
    @patch("localci.utils.resources.ResourceMonitor._try_import_psutil")
    def test_check_thresholds_healthy(self, mock_import, mock_subprocess):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="")
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 42.0
        mem = MagicMock()
        mem.percent = 60.0
        mem.available = 4 * 1024**3
        mock_psutil.virtual_memory.return_value = mem
        disk = MagicMock()
        disk.free = 100 * 1024**3
        mock_psutil.disk_usage.return_value = disk
        mock_import.return_value = mock_psutil

        monitor = ResourceMonitor()
        ok, warnings = monitor.check_thresholds(
            cpu_threshold=100.0,
            memory_threshold=100.0,
            disk_min_gb=0.0,
        )
        assert ok is True
        assert len(warnings) == 0
