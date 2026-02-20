"""Translate :class:`MatrixEntry` objects into :class:`ActCommand` invocations.

This module bridges the workflow-analyzer output (Issue 2) with the
executor engine (Issue 3), mapping parsed matrix entries to the correct
``act`` CLI flags.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

from localci.core.executor import ActCommand
from localci.core.workflow import MatrixEntry

logger = logging.getLogger(__name__)

# Known runner label -> Ubuntu version mappings
RUNNER_OS_MAP: dict[str, str] = {
    "ubuntu-latest": "24.04",
    "ubuntu-24.04": "24.04",
    "ubuntu-22.04": "22.04",
    "ubuntu-20.04": "20.04",
}


class ActCommandBuilder:
    """Build ``act`` commands from parsed :class:`MatrixEntry` objects.

    Handles the translation between workflow analyzer output and the
    ``act`` CLI flags.

    Usage::

        builder = ActCommandBuilder(
            workflow_file=Path(".github/workflows/ci.yml"),
            project_dir=Path("."),
        )
        cmd = builder.build(
            matrix_entry, image_tag="capy-ubuntu-25.04-gcc15:latest"
        )
        print(cmd.display())
    """

    def __init__(
        self,
        workflow_file: Path,
        project_dir: Path = Path("."),
        job_id: str = "build",
        repo_full_name: str = "cppalliance/capy",
        default_env: Optional[dict[str, str]] = None,
        default_secrets: Optional[dict[str, str]] = None,
    ) -> None:
        self.workflow_file = workflow_file
        self.project_dir = project_dir
        self.job_id = job_id
        self.repo_full_name = repo_full_name
        self.default_env = default_env or {}
        self.default_secrets = default_secrets or {}

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def build(
        self,
        entry: MatrixEntry,
        image_tag: Optional[str] = None,
        dryrun: bool = False,
        verbose: bool = False,
        extra_env: Optional[dict[str, str]] = None,
    ) -> ActCommand:
        """Build an :class:`ActCommand` for a specific matrix entry.

        Parameters
        ----------
        entry:
            Parsed matrix entry from :class:`WorkflowAnalyzer`.
        image_tag:
            Docker image tag to use (from registry / local).
        dryrun:
            Preview without executing.
        verbose:
            Enable debug output.
        extra_env:
            Additional environment variables.

        Returns
        -------
        ActCommand
            Fully configured command ready to execute.
        """
        # Matrix filters
        matrix_filters = self._build_matrix_filters(entry)

        # Runner-to-image mapping
        runner_mappings = self._build_runner_mappings(entry, image_tag)

        # Environment variables
        env = {**self.default_env}
        if entry.compiler.cc:
            env["CC"] = entry.compiler.cc
        if entry.compiler.cxx:
            env["CXX"] = entry.compiler.cxx
        if extra_env:
            env.update(extra_env)

        # Secrets
        secrets = {**self.default_secrets}
        secrets.setdefault("GITHUB_TOKEN", "local-ci-token")

        # Architecture
        container_arch: Optional[str] = None
        if entry.architecture == "x86":
            container_arch = "linux/386"

        # Event file
        event_file = self._create_event_file()

        return ActCommand(
            workflow_file=self.workflow_file,
            job_id=self.job_id,
            matrix_filters=matrix_filters,
            runner_mappings=runner_mappings,
            pull=False,
            offline=True,
            privileged=True,
            rm=True,
            dryrun=dryrun,
            verbose=verbose,
            env=env,
            secrets=secrets,
            event_file=event_file,
            container_architecture=container_arch,
            workdir=self.project_dir,
        )

    # -----------------------------------------------------------------
    # Matrix filters
    # -----------------------------------------------------------------

    def _build_matrix_filters(self, entry: MatrixEntry) -> dict[str, str]:
        """Extract matrix filter key-value pairs from *entry*.

        These are passed as ``--matrix`` flags to ``act`` to select
        the specific matrix entry to run.
        """
        filters: dict[str, str] = {}

        raw = entry.raw
        if "compiler" in raw:
            filters["compiler"] = str(raw["compiler"])
        if "version" in raw:
            filters["version"] = str(raw["version"])
        if "name" in raw:
            filters["name"] = str(raw["name"])

        return filters

    # -----------------------------------------------------------------
    # Runner-to-image mapping
    # -----------------------------------------------------------------

    def _build_runner_mappings(
        self,
        entry: MatrixEntry,
        image_tag: Optional[str],
    ) -> dict[str, str]:
        """Map GitHub runner labels to Docker images.

        For container-based jobs ``act`` uses the container image
        directly, but we still map the runner for consistency.
        For runner-based jobs the ``-P`` mapping is essential.
        """
        mappings: dict[str, str] = {}

        if not image_tag:
            return mappings

        runs_on = entry.runs_on
        mappings[runs_on] = image_tag

        # Also map common aliases
        if runs_on == "ubuntu-latest":
            mappings["ubuntu-24.04"] = image_tag
        elif runs_on == "ubuntu-24.04":
            mappings["ubuntu-latest"] = image_tag

        return mappings

    # -----------------------------------------------------------------
    # Event file
    # -----------------------------------------------------------------

    def _create_event_file(self) -> Path:
        """Create minimal event payload for ``act``.

        ``act`` needs an event file to simulate the GitHub event.
        Uses :func:`tempfile.NamedTemporaryFile` (with ``delete=False``)
        to avoid the TOCTOU race of the deprecated ``mktemp``.
        """
        event = {
            "push": {
                "ref": "refs/heads/develop",
                "repository": {
                    "full_name": self.repo_full_name,
                },
            },
        }

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="localci-event-",
            delete=False,
        )
        tmp.write(json.dumps(event))
        tmp.close()
        return Path(tmp.name)
