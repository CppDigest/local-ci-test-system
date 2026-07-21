"""Translate :class:`MatrixEntry` objects into :class:`ActCommand` invocations.

This module bridges the workflow-analyzer output (Issue 2) with the
executor engine (Issue 3), mapping parsed matrix entries to the correct
``act`` CLI flags.
"""

from __future__ import annotations

import json
import logging
import shlex
import tempfile
from pathlib import Path

from localci.core.config import (
    GENERIC_NATIVE_IMAGE_PREFIX,
    GENERIC_REPO_FULL_NAME,
    CacheConfig,
    ResolvedCachePaths,
)
from localci.core.executor import ActCommand
from localci.core.github_token import SENTINEL_GITHUB_TOKEN
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
        repo_full_name: str | None = None,
        native_image_prefix: str | None = None,
        default_env: dict[str, str] | None = None,
        default_secrets: dict[str, str] | None = None,
        offline: bool = False,
        act_version: tuple[int, int, int] | None = None,
    ) -> None:
        self.workflow_file = workflow_file
        self.project_dir = project_dir
        self.job_id = job_id
        self.repo_full_name = (
            GENERIC_REPO_FULL_NAME if repo_full_name is None else repo_full_name
        )
        self.native_image_prefix = (
            GENERIC_NATIVE_IMAGE_PREFIX
            if native_image_prefix is None
            else native_image_prefix
        )
        self.default_env = default_env or {}
        self.default_secrets = default_secrets or {}
        self.offline = offline
        self.act_version = act_version

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def build(
        self,
        entry: MatrixEntry,
        image_tag: str | None = None,
        dryrun: bool = False,
        verbose: bool = False,
        extra_env: dict[str, str] | None = None,
        workflow_file: Path | None = None,
        action_cache_path: Path | None = None,
        resolved_cache_paths: ResolvedCachePaths | None = None,
        cache_config: CacheConfig | None = None,
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
        workflow_file:
            Override workflow file path (e.g. patched workflow with container image).
        action_cache_path:
            Per-job act action cache directory (avoids parallel races in shared cache).
        resolved_cache_paths:
            Phase 2: resolved host cache paths for ccache/boost/cmake bind mounts.
        cache_config:
            Phase 2: cache config (for CCACHE_MAXSIZE etc.).

        Returns
        -------
        ActCommand
            Fully configured command ready to execute.
        """
        wf_path = workflow_file if workflow_file is not None else self.workflow_file
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

        # Phase 2: cache bind mounts and env
        container_options: str | None = None
        if resolved_cache_paths is not None:
            mount_parts: list[str] = []
            if resolved_cache_paths.ccache_host is not None:
                mount_parts.append(
                    f"-v {shlex.quote(str(resolved_cache_paths.ccache_host))}:{shlex.quote(str(resolved_cache_paths.ccache_container))}"
                )
            if resolved_cache_paths.boost_host is not None:
                mount_parts.append(
                    f"-v {shlex.quote(str(resolved_cache_paths.boost_host))}:{shlex.quote(str(resolved_cache_paths.boost_container))}"
                )
            if resolved_cache_paths.cmake_host is not None:
                mount_parts.append(
                    f"-v {shlex.quote(str(resolved_cache_paths.cmake_host))}:{shlex.quote(str(resolved_cache_paths.cmake_container))}"
                )
            if resolved_cache_paths.b2_source_host is not None:
                mount_parts.append(
                    f"-v {shlex.quote(str(resolved_cache_paths.b2_source_host))}:{shlex.quote(str(resolved_cache_paths.b2_source_container))}"
                )
            if resolved_cache_paths.apt_host is not None:
                mount_parts.append(
                    f"-v {shlex.quote(str(resolved_cache_paths.apt_host))}:{shlex.quote(str(resolved_cache_paths.apt_container))}"
                )
            if mount_parts:
                container_options = " ".join(mount_parts)
            if resolved_cache_paths.ccache_host is not None:
                env["CCACHE_DIR"] = resolved_cache_paths.ccache_container
                if cache_config and cache_config.ccache.enabled:
                    env["CCACHE_MAXSIZE"] = cache_config.ccache.max_size
                    env["CCACHE_COMPRESS"] = (
                        "1" if cache_config.ccache.compress else "0"
                    )
                    # Same approach as Boost-hands-on-exp: wrap compiler with ccache so
                    # b2 and other build steps use ccache when they invoke CC/CXX.
                    if env.get("CC"):
                        env["CC"] = f"ccache {env['CC']}"
                    else:
                        env["CC"] = "ccache gcc"
                    if env.get("CXX"):
                        env["CXX"] = f"ccache {env['CXX']}"
                    else:
                        env["CXX"] = "ccache g++"
            if resolved_cache_paths.boost_host is not None:
                env["BOOST_ROOT"] = resolved_cache_paths.boost_container
            if resolved_cache_paths.cmake_host is not None:
                env["LOCALCI_CMAKE_CACHE_DIR"] = resolved_cache_paths.cmake_container
            if resolved_cache_paths.b2_source_host is not None:
                env["LOCALCI_B2_SOURCE_DIR"] = resolved_cache_paths.b2_source_container

        # Secrets: copy caller-provided secrets; only fill GITHUB_TOKEN when absent
        # (setdefault — never override a real token from the orchestrator path).
        secrets = {**self.default_secrets}
        secrets.setdefault("GITHUB_TOKEN", SENTINEL_GITHUB_TOKEN)

        # Architecture: request linux/386 only when using a generic image
        # (e.g. ubuntu:24.04). Project-specific native images (e.g. capy x86)
        # are amd64 with multilib, so we must not request 386 when using them.
        container_arch: str | None = None
        if entry.architecture == "x86":
            uses_native_image = (
                bool(self.native_image_prefix)
                and bool(image_tag)
                and str(image_tag).startswith(self.native_image_prefix)
            )
            if not uses_native_image:
                container_arch = "linux/386"

        # Event file
        event_file = self._create_event_file()

        cmd = ActCommand(
            workflow_file=wf_path,
            job_id=self.job_id,
            matrix_filters=matrix_filters,
            runner_mappings=runner_mappings,
            pull=False,
            offline=self.offline,
            privileged=True,
            rm=True,
            dryrun=dryrun,
            verbose=verbose,
            env=env,
            secrets=secrets,
            event_file=event_file,
            container_architecture=container_arch,
            action_cache_path=action_cache_path,
            container_options=container_options,
            workdir=self.project_dir,
            act_version=self.act_version,
        )
        cmd._executor_owned_event_file = True
        return cmd

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
        image_tag: str | None,
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

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="localci-event-",
            delete=False,
        ) as tmp:
            tmp.write(json.dumps(event))
            return Path(tmp.name)
