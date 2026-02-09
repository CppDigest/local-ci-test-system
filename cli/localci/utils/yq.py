"""Wrapper for yq (mikefarah/yq) YAML processor with PyYAML fallback.

Provides structured queries against GitHub Actions YAML files.  When the
``yq`` binary is available it is used for the low-level :meth:`query`
method; all high-level helpers use PyYAML directly so the module works
out-of-the-box without any external tool.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
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

    Uses PyYAML for all high-level helpers (no external dependency).
    The low-level :meth:`query` method delegates to ``yq`` when it is
    available, falling back to :exc:`YqNotFoundError` otherwise.
    """

    def __init__(self) -> None:
        self._yq_path: Optional[str] = shutil.which("yq")
        if self._yq_path:
            logger.debug("yq found at %s", self._yq_path)
        else:
            logger.debug("yq not found -- using PyYAML fallback for all queries")
        self._file_cache: dict[Path, dict] = {}

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def has_yq(self) -> bool:
        """``True`` when the ``yq`` binary is available on ``PATH``."""
        return self._yq_path is not None

    # -----------------------------------------------------------------
    # Low-level helpers
    # -----------------------------------------------------------------

    def _load(self, file: Path) -> dict:
        """Load a YAML file via PyYAML (result is cached)."""
        resolved = file.resolve()
        if resolved not in self._file_cache:
            if not file.exists():
                raise FileNotFoundError(f"Workflow file not found: {file}")
            with open(file, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            self._file_cache[resolved] = data if isinstance(data, dict) else {}
        return self._file_cache[resolved]

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
    # High-level helpers (all use PyYAML -- no yq required)
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
