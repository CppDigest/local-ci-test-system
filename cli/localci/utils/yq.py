"""Wrapper for yq (mikefarah/yq) YAML processor with PyYAML fallback.

Provides structured queries against GitHub Actions YAML files.

**Linux**: ``yq`` is the primary YAML parser (as specified in the Design
Guide).  PyYAML is used as a fallback when ``yq`` is not installed.

**Other platforms** (Windows, macOS): ``yq`` is preferred when available,
otherwise PyYAML is used.

The low-level :meth:`query` method always requires the ``yq`` binary
regardless of platform.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


# =====================================================================
# Errors
# =====================================================================


class YqError(Exception):
    """Error from yq execution."""

    def __init__(self, expression: str, stderr: str):
        self.expression = expression
        self.stderr = stderr
        super().__init__(f"yq error for '{expression}': {stderr}")


class YqNotFoundError(Exception):
    """yq is not installed (raised only by :meth:`query`)."""

    def __init__(self) -> None:
        super().__init__(
            "yq is not installed.\n"
            "Install with:\n"
            "  Windows:  choco install yq\n"
            "  Linux:    sudo snap install yq\n"
            "  macOS:    brew install yq\n"
            "\n"
            "Note: yq is optional -- all built-in commands work without it."
        )


# =====================================================================
# YqWrapper
# =====================================================================


class YqWrapper:
    """YAML query wrapper.

    On **Linux**, ``yq`` is the primary parser for all YAML loading (as
    specified by the Design Guide).  PyYAML is used as a fallback when
    ``yq`` is not installed or when a ``yq`` invocation fails.

    On **other platforms**, ``yq`` is preferred when available, with
    PyYAML as the automatic fallback.

    The low-level :meth:`query` method always requires the ``yq`` binary
    regardless of platform.
    """

    def __init__(self) -> None:
        self._is_linux: bool = sys.platform.startswith("linux")
        self._file_cache: dict[Path, dict] = {}

        raw_path: Optional[str] = shutil.which("yq")
        self._yq_path: Optional[str] = None

        if raw_path:
            flavour = self._detect_yq_flavour(raw_path)
            if flavour == "mikefarah":
                self._yq_path = raw_path
                logger.debug("yq (mikefarah) found at %s", raw_path)
            else:
                logger.debug(
                    "yq at %s is not mikefarah/yq (detected: %s) -- "
                    "using PyYAML fallback. Install mikefarah/yq for best results: "
                    "sudo snap install yq",
                    raw_path, flavour,
                )

        if not self._yq_path and self._is_linux:
            logger.debug(
                "mikefarah/yq not available on Linux -- using PyYAML fallback. "
                "Install with: sudo snap install yq"
            )

    @staticmethod
    def _detect_yq_flavour(yq_path: str) -> str:
        """Return 'mikefarah', 'kislyuk', or 'unknown' based on --version output.

        mikefarah/yq:  'yq (https://github.com/mikefarah/yq/) version v4.x.x'
        kislyuk/yq:    'yq x.x.x' (no URL) — Python wrapper around jq
        """
        try:
            result = subprocess.run(
                [yq_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_str = (result.stdout + result.stderr).lower()
            if "mikefarah" in version_str:
                return "mikefarah"
            if "kislyuk" in version_str or "jq" in version_str:
                return "kislyuk"
            # mikefarah/yq 4.x prints the URL; kislyuk prints nothing with --version
            # If the output looks like 'yq version v4.' it's mikefarah
            if "version v" in version_str and "github.com/mikefarah" not in version_str:
                return "unknown"
            if "github.com/mikefarah" in version_str:
                return "mikefarah"
            return "unknown"
        except Exception:
            return "unknown"

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def has_yq(self) -> bool:
        """``True`` when the ``yq`` binary is available on ``PATH``."""
        return self._yq_path is not None

    @property
    def is_linux(self) -> bool:
        """``True`` when running on a Linux system."""
        return self._is_linux

    # -----------------------------------------------------------------
    # Low-level helpers
    # -----------------------------------------------------------------

    def _load(self, file: Path) -> dict:
        """Load and cache a YAML file.

        On Linux, uses ``yq`` as the primary parser (per Design Guide),
        falling back to PyYAML if ``yq`` is unavailable or fails.
        On other platforms, uses ``yq`` when available, PyYAML otherwise.

        The result is cached so that repeated queries against the same
        file do not re-parse.
        """
        resolved = file.resolve()
        if resolved not in self._file_cache:
            if not file.exists():
                raise FileNotFoundError(f"Workflow file not found: {file}")

            data = None

            # Prefer yq when available (required on Linux per Design Guide)
            if self._yq_path:
                try:
                    data = self._load_via_yq(file)
                    logger.debug("Loaded %s via yq", file)
                except Exception as exc:
                    if self._is_linux:
                        logger.warning(
                            "yq failed for %s (%s), falling back to PyYAML",
                            file, exc,
                        )
                    else:
                        logger.debug(
                            "yq failed for %s (%s), falling back to PyYAML",
                            file, exc,
                        )
                    data = None

            # Fallback to PyYAML
            if data is None:
                data = self._load_via_pyyaml(file)
                logger.debug("Loaded %s via PyYAML", file)

            self._file_cache[resolved] = data
        return self._file_cache[resolved]

    def _load_via_yq(self, file: Path) -> dict:
        """Load a YAML file by running ``yq -o json '.' <file>``.

        Returns the entire file content as a Python dict.
        """
        result = subprocess.run(
            [self._yq_path, "-o", "json", ".", str(file)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise YqError(".", result.stderr.strip())

        output = result.stdout.strip()
        if not output or output == "null":
            return {}

        data = json.loads(output)
        return data if isinstance(data, dict) else {}

    def _load_via_pyyaml(self, file: Path) -> dict:
        """Load a YAML file using PyYAML (fallback)."""
        with open(file, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}

    def clear_cache(self) -> None:
        """Clear the file cache (useful between test runs)."""
        self._file_cache.clear()

    # -----------------------------------------------------------------
    # Low-level yq query (requires yq binary)
    # -----------------------------------------------------------------

    def query(self, file: Path, expression: str) -> Any:
        """Execute a raw yq expression against *file*.

        Requires the ``yq`` binary.  Raises :exc:`YqNotFoundError` when
        ``yq`` is not installed.

        Returns
        -------
        dict | list | str | int | bool | None
            Parsed JSON output.
        """
        if not self._yq_path:
            raise YqNotFoundError()

        if not file.exists():
            raise FileNotFoundError(f"Workflow file not found: {file}")

        result = subprocess.run(
            [self._yq_path, "-o", "json", expression, str(file)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise YqError(expression, result.stderr.strip())

        output = result.stdout.strip()
        if not output or output == "null":
            return None

        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output

    def version(self) -> Optional[str]:
        """Return the ``yq --version`` string, or *None* if yq is absent."""
        if not self._yq_path:
            return None
        result = subprocess.run(
            [self._yq_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()

    # =================================================================
    # High-level helpers (use yq on Linux, PyYAML fallback elsewhere)
    # =================================================================

    def workflow_name(self, file: Path) -> str:
        """Extract workflow name."""
        data = self._load(file)
        return data.get("name") or file.stem

    def events(self, file: Path) -> list[str]:
        """Extract trigger events."""
        data = self._load(file)
        # PyYAML parses bare ``on:`` as the boolean key ``True``
        on = data.get("on") or data.get(True, {})
        if isinstance(on, dict):
            return list(on.keys())
        if isinstance(on, list):
            return on
        if isinstance(on, str):
            return [on]
        return []

    def event_branches(self, file: Path, event: str) -> list[str]:
        """Extract branches for *event*."""
        data = self._load(file)
        on = data.get("on") or data.get(True, {})
        if isinstance(on, dict):
            evt = on.get(event, {})
            if isinstance(evt, dict):
                branches = evt.get("branches", [])
                return branches if isinstance(branches, list) else []
        return []

    def global_env(self, file: Path) -> dict[str, str]:
        """Extract global environment variables."""
        data = self._load(file)
        env = data.get("env", {})
        return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}

    def concurrency(self, file: Path) -> Optional[dict]:
        """Extract concurrency configuration."""
        data = self._load(file)
        return data.get("concurrency")

    def job_names(self, file: Path) -> list[str]:
        """Extract all job IDs."""
        data = self._load(file)
        jobs = data.get("jobs", {})
        return list(jobs.keys()) if isinstance(jobs, dict) else []

    def job_data(self, file: Path, job_id: str) -> dict:
        """Extract full job data dict for *job_id*."""
        data = self._load(file)
        jobs = data.get("jobs", {})
        return jobs.get(job_id, {})

    def job_needs(self, file: Path, job_id: str) -> list[str]:
        """Extract job dependencies."""
        jd = self.job_data(file, job_id)
        needs = jd.get("needs")
        if isinstance(needs, list):
            return [str(n) for n in needs]
        if isinstance(needs, str):
            return [needs]
        return []

    def job_condition(self, file: Path, job_id: str) -> Optional[str]:
        """Extract job ``if`` condition."""
        return self.job_data(file, job_id).get("if")

    def job_runs_on(self, file: Path, job_id: str) -> str:
        """Extract job ``runs-on``."""
        return self.job_data(file, job_id).get("runs-on", "ubuntu-latest")

    def job_container(self, file: Path, job_id: str) -> Optional[dict]:
        """Extract job container configuration."""
        container = self.job_data(file, job_id).get("container")
        if isinstance(container, str):
            return {"image": container}
        return container

    def job_timeout(self, file: Path, job_id: str) -> int:
        """Extract job timeout in minutes."""
        return self.job_data(file, job_id).get("timeout-minutes", 60)

    def job_defaults(self, file: Path, job_id: str) -> Optional[dict]:
        """Extract job defaults."""
        return self.job_data(file, job_id).get("defaults")

    def job_env(self, file: Path, job_id: str) -> dict[str, str]:
        """Extract job environment variables."""
        env = self.job_data(file, job_id).get("env", {})
        return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}

    def matrix_strategy(self, file: Path, job_id: str) -> Optional[dict]:
        """Extract matrix strategy."""
        return self.job_data(file, job_id).get("strategy")

    def matrix_include(self, file: Path, job_id: str) -> list[dict]:
        """Extract matrix include entries."""
        strategy = self.matrix_strategy(file, job_id)
        if strategy and isinstance(strategy, dict):
            matrix = strategy.get("matrix", {})
            if isinstance(matrix, dict):
                return matrix.get("include", [])
        return []

    def matrix_entry(self, file: Path, job_id: str, index: int) -> dict:
        """Extract specific matrix entry by *index*."""
        entries = self.matrix_include(file, job_id)
        if 0 <= index < len(entries):
            return entries[index]
        return {}

    def matrix_count(self, file: Path, job_id: str) -> int:
        """Count matrix entries."""
        return len(self.matrix_include(file, job_id))

    def matrix_filter_by_field(
        self, file: Path, job_id: str, field: str, value: str
    ) -> list[dict]:
        """Filter matrix entries by a field value."""
        return [
            e
            for e in self.matrix_include(file, job_id)
            if str(e.get(field, "")) == value
        ]

    def steps(self, file: Path, job_id: str) -> list[dict]:
        """Extract job steps."""
        return self.job_data(file, job_id).get("steps", [])

    def step_count(self, file: Path, job_id: str) -> int:
        """Count steps in a job."""
        return len(self.steps(file, job_id))

    def actions_used(self, file: Path, job_id: str) -> list[str]:
        """Extract all action references in a job."""
        return [
            s["uses"]
            for s in self.steps(file, job_id)
            if "uses" in s
        ]
