"""Wrapper for yq (mikefarah/yq) YAML processor.

Provides structured queries against GitHub Actions YAML files using
``yq`` as the primary parser (as required by the Design Guide).  When
``yq`` is not installed, a PyYAML fallback handles simple dot-path
expressions so that development and testing can proceed.

All high-level helpers delegate to :meth:`query`, which dispatches to
``yq`` (subprocess) or the built-in Python evaluator automatically.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, Optional

import yaml

from localci.errors import YqError, YqNotFoundError

logger = logging.getLogger(__name__)


class YqFallbackWarning(UserWarning):
    """PyYAML fallback is active because mikefarah/yq v4+ is missing or wrong flavour."""


# =====================================================================
# YqWrapper
# =====================================================================


class YqWrapper:
    """YAML query wrapper -- ``yq`` primary, PyYAML fallback.

    The Design Guide specifies ``yq`` as the YAML parser.  This wrapper
    uses the ``yq`` binary for **all** queries when it is available.
    When ``yq`` is absent a PyYAML-based fallback handles the simple
    dot-path expressions used by the built-in high-level helpers, and
    emits :exc:`YqFallbackWarning` plus a log line so the user knows to
    install ``mikefarah/yq`` v4+.
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
                install_hint = (
                    "sudo snap install yq" if self._is_linux
                    else "https://github.com/mikefarah/yq/releases"
                )
                detail = (
                    f"The `yq` on PATH at {raw_path!r} is not mikefarah/yq v4+ "
                    f"(detected: {flavour}). "
                    f"Install mikefarah/yq: {install_hint}"
                )
                self._warn_pyyaml_fallback(detail)
        else:
            # No yq binary found at all
            if self._is_linux:
                detail = (
                    "No mikefarah/yq v4+ binary was found on PATH on Linux. "
                    "Example: sudo snap install yq"
                )
            else:
                detail = (
                    "No mikefarah/yq v4+ binary was found on PATH. "
                    "See https://github.com/mikefarah/yq#install"
                )
            self._warn_pyyaml_fallback(detail)

    def _warn_pyyaml_fallback(self, detail: str) -> None:
        """Log and emit a visible warning when PyYAML fallback is used."""
        msg = (
            "Local CI is using the PyYAML YAML fallback (limited yq expression "
            "support). "
            f"{detail} "
            "Required: mikefarah/yq v4 or newer — "
            "https://github.com/mikefarah/yq#install"
        )
        logger.warning(msg.replace("\n", " "))
        warnings.warn(msg, YqFallbackWarning, stacklevel=3)

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
            if result.returncode != 0:
                return "unknown"
            version_str = (result.stdout + result.stderr).lower()
            if "mikefarah" in version_str or "github.com/mikefarah" in version_str:
                return "mikefarah"
            if "kislyuk" in version_str or "jq" in version_str:
                return "kislyuk"
            # mikefarah/yq 4.x often prints "yq ... version v4.x"; some builds omit the URL
            if "version v" in version_str:
                return "mikefarah"
            return "unknown"
        except Exception:
            return "unknown"

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def has_yq(self) -> bool:
        """``True`` when the ``yq`` binary is on ``PATH``."""
        return self._yq_path is not None

    # -----------------------------------------------------------------
    # Core query method
    # -----------------------------------------------------------------

    def query(self, file: Path, expression: str) -> Any:
        """Execute a yq expression against *file*.

        Uses the ``yq`` binary when available; otherwise falls back to
        a simple Python-based expression evaluator (supports dot-path
        navigation, ``| keys``, and ``| length``).

        Returns
        -------
        dict | list | str | int | bool | None
            Parsed result.
        """
        if not file.exists():
            raise FileNotFoundError(f"Workflow file not found: {file}")

        if self._yq_path:
            return self._query_yq(file, expression)
        return self._query_python(file, expression)

    # -----------------------------------------------------------------
    # yq subprocess backend
    # -----------------------------------------------------------------

    def _query_yq(self, file: Path, expression: str) -> Any:
        """Execute *expression* via ``yq -o json``."""
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
            # yq may return unquoted strings
            return output

    # -----------------------------------------------------------------
    # PyYAML fallback backend
    # -----------------------------------------------------------------

    def _load_yaml(self, file: Path) -> dict:
        """Load *file* via PyYAML (result is cached).

        Caller must ensure file exists (e.g. query() checks before calling).
        """
        resolved = file.resolve()
        if resolved not in self._file_cache:
            with open(file, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                logger.warning(
                    "YAML root is not a dict (got %s) in %s; treating as empty.",
                    type(data).__name__,
                    file,
                )
                data = {}
            self._file_cache[resolved] = data
        return self._file_cache[resolved]

    def _query_python(self, file: Path, expression: str) -> Any:
        """Evaluate *expression* against PyYAML-loaded data.

        Supports:
        - Dot-path navigation: ``.name``, ``.jobs.build.strategy``
        - Array indexing: ``.jobs.build.strategy.matrix.include[0]``
        - Pipe ``keys``: ``.jobs | keys``
        - Pipe ``length``: ``.jobs.build.steps | length``

        Complex yq expressions (``select``, ``test``, etc.) are not
        supported and return ``None`` with a logged warning.
        """
        data = self._load_yaml(file)

        # Split on top-level pipe (not inside brackets/quotes)
        pipe_parts = [p.strip() for p in expression.split(" | ")]
        path_expr = pipe_parts[0]
        pipe_ops = pipe_parts[1:]

        # Strip yq alternative-operator wrapper: (.path // default) → .path
        # _navigate already returns None for missing keys, and pipe ops handle None safely.
        path_expr = re.sub(r"^\((.+?)\s*//.*\)$", r"\1", path_expr)

        result = self._navigate(data, path_expr)

        for op in pipe_ops:
            if op == "keys":
                result = list(result.keys()) if isinstance(result, dict) else []
            elif op == "length":
                result = len(result) if result else 0
            else:
                logger.warning(
                    "Complex yq expression not supported in fallback mode: %s "
                    "(install yq for full support)",
                    expression,
                )
                return None

        return result

    @staticmethod
    def _navigate(data: Any, path: str) -> Any:
        """Navigate a dot-separated path through nested dicts/lists."""
        if not path or path == ".":
            return data

        path = path.lstrip(".")
        if not path:
            return data

        current = data
        segments = path.split(".")

        for segment in segments:
            if current is None:
                return None

            # Array index: e.g. include[0]
            match = re.match(r"^(.+)\[(\d+)]$", segment)
            if match:
                key, idx = match.group(1), int(match.group(2))
                current = current.get(key) if isinstance(current, dict) else None
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
                continue

            if isinstance(current, dict):
                # PyYAML parses bare `on:` as boolean True
                if segment == "on" and segment not in current and True in current:
                    current = current[True]
                else:
                    current = current.get(segment)
            else:
                return None

        return current

    def clear_cache(self) -> None:
        """Clear the PyYAML file cache."""
        self._file_cache.clear()

    # -----------------------------------------------------------------
    # Misc
    # -----------------------------------------------------------------

    def version(self) -> Optional[str]:
        """Return ``yq --version`` string, or *None* if yq is absent."""
        if not self._yq_path:
            return None
        try:
            result = subprocess.run(
                [self._yq_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    # =================================================================
    # High-level helpers -- all delegate to self.query()
    # =================================================================

    def workflow_name(self, file: Path) -> str:
        """Extract workflow name."""
        return self.query(file, ".name") or file.stem

    def events(self, file: Path) -> list[str]:
        """Extract trigger events."""
        on = self.query(file, ".on")
        if isinstance(on, dict):
            return list(on.keys())
        if isinstance(on, list):
            return on
        if isinstance(on, str):
            return [on]
        return []

    def event_branches(self, file: Path, event: str) -> list[str]:
        """Extract branches for *event*."""
        branches = self.query(file, f".on.{event}.branches")
        if isinstance(branches, list):
            return branches
        return []

    def global_env(self, file: Path) -> dict[str, str]:
        """Extract global environment variables."""
        env = self.query(file, ".env")
        if isinstance(env, dict):
            return {str(k): str(v) for k, v in env.items()}
        return {}

    def concurrency(self, file: Path) -> Optional[dict]:
        """Extract concurrency configuration."""
        return self.query(file, ".concurrency")

    def job_names(self, file: Path) -> list[str]:
        """Extract all job IDs."""
        result = self.query(file, "(.jobs // {}) | keys")
        return result if isinstance(result, list) else []

    def job_data(self, file: Path, job_id: str) -> dict:
        """Extract full job data dict for *job_id*."""
        result = self.query(file, f".jobs.{job_id}")
        return result if isinstance(result, dict) else {}

    def job_needs(self, file: Path, job_id: str) -> list[str]:
        """Extract job dependencies."""
        needs = self.query(file, f".jobs.{job_id}.needs")
        if isinstance(needs, list):
            return [str(n) for n in needs]
        if isinstance(needs, str):
            return [needs]
        return []

    def job_condition(self, file: Path, job_id: str) -> Optional[str]:
        """Extract job ``if`` condition."""
        return self.query(file, f".jobs.{job_id}.if")

    def job_runs_on(self, file: Path, job_id: str) -> str:
        """Extract job ``runs-on``."""
        return self.query(file, f'.jobs.{job_id}.runs-on') or "ubuntu-latest"

    def job_container(self, file: Path, job_id: str) -> Optional[dict]:
        """Extract job container configuration."""
        container = self.query(file, f".jobs.{job_id}.container")
        if isinstance(container, str):
            return {"image": container}
        return container

    def job_timeout(self, file: Path, job_id: str) -> int:
        """Extract job timeout in minutes."""
        return self.query(file, f".jobs.{job_id}.timeout-minutes") or 60

    def job_defaults(self, file: Path, job_id: str) -> Optional[dict]:
        """Extract job defaults."""
        return self.query(file, f".jobs.{job_id}.defaults")

    def job_env(self, file: Path, job_id: str) -> dict[str, str]:
        """Extract job environment variables."""
        env = self.query(file, f".jobs.{job_id}.env")
        if isinstance(env, dict):
            return {str(k): str(v) for k, v in env.items()}
        return {}

    def matrix_strategy(self, file: Path, job_id: str) -> Optional[dict]:
        """Extract matrix strategy."""
        return self.query(file, f".jobs.{job_id}.strategy")

    def matrix_include(self, file: Path, job_id: str) -> list[dict]:
        """Extract matrix include entries."""
        result = self.query(file, f".jobs.{job_id}.strategy.matrix.include")
        return result if isinstance(result, list) else []

    def matrix_entry(self, file: Path, job_id: str, index: int) -> dict:
        """Extract specific matrix entry by *index*."""
        result = self.query(
            file, f".jobs.{job_id}.strategy.matrix.include[{index}]"
        )
        return result if isinstance(result, dict) else {}

    def matrix_count(self, file: Path, job_id: str) -> int:
        """Count matrix entries."""
        result = self.query(
            file, f".jobs.{job_id}.strategy.matrix.include | length"
        )
        return result if isinstance(result, int) else 0

    def matrix_filter_by_field(
        self, file: Path, job_id: str, field_name: str, value: str
    ) -> list[dict]:
        """Filter matrix entries by a field value."""
        # For yq we can use select(); for fallback, use Python filtering
        entries = self.matrix_include(file, job_id)
        return [e for e in entries if str(e.get(field_name, "")) == value]

    def steps(self, file: Path, job_id: str) -> list[dict]:
        """Extract job steps."""
        result = self.query(file, f".jobs.{job_id}.steps")
        return result if isinstance(result, list) else []

    def step_count(self, file: Path, job_id: str) -> int:
        """Count steps in a job."""
        result = self.query(file, f".jobs.{job_id}.steps | length")
        return result if isinstance(result, int) else 0

    def actions_used(self, file: Path, job_id: str) -> list[str]:
        """Extract all action references in a job."""
        step_list = self.steps(file, job_id)
        return [s["uses"] for s in step_list if "uses" in s]
