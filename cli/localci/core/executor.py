"""Job executor: data models and act-based execution engine.

Provides :class:`ActCommand` for building ``act`` CLI invocations,
:class:`JobResult` for capturing outcomes, and :class:`JobExecutor`
for the full lifecycle of running a single CI job locally.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import IO

from localci.errors import ActNotFoundError, DockerNotAvailableError

_DEFAULT_LOGS_DIR = Path.home() / ".localci" / "logs"

logger = logging.getLogger(__name__)

_INVALID_SECRET_KEY_CHARS = frozenset("=\n\r\0")


def _validate_secret_key(key: str) -> None:
    """Reject secret keys that would corrupt act's godotenv secret file."""
    if any(char in key for char in _INVALID_SECRET_KEY_CHARS):
        raise ValueError(f"Invalid secret key: {key!r}")


def _format_secret_file_line(key: str, value: str) -> str:
    """Format one KEY=VALUE line for act's godotenv ``--secret-file`` parser."""
    _validate_secret_key(key)
    if "\0" in value:
        raise ValueError(f"Invalid secret value for key {key!r}")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'{key}="{escaped}"\n'


# Substrings (matched case-insensitively) for summarizing failed job output.
_ERROR_EXTRACT_KEYWORDS = (
    "error:",
    "fatal:",
    "failed",
    "error[",
    "undefined reference",
    "no such file",
    "cannot find",
    "compilation failed",
)

# Public for tests: substring signals for auth/API failures in act output.
# HTTP 4xx status codes use _HTTP_STATUS_PATTERN (word-boundary) to avoid
# false positives such as "4010" or "port 40100".
AUTH_ERROR_EXTRACT_KEYWORDS = (
    "unauthorized",
    "forbidden",
    "rate limit",
)
_HTTP_STATUS_PATTERN = re.compile(r"\b4\d{2}\b")


# =====================================================================
# Enums
# =====================================================================


class JobStatus(Enum):
    """Job execution status."""

    PENDING = "pending"
    PREPARING = "preparing"  # Loading image
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"  # Internal error (not test failure)
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


# =====================================================================
# Data-classes
# =====================================================================


@dataclass
class JobResult:
    """Complete result of a job execution."""

    # Identity
    job_id: str  # e.g., "build"
    matrix_index: int
    matrix_name: str  # e.g., "GCC 15: C++20"

    # Status
    status: JobStatus = JobStatus.PENDING
    exit_code: int | None = None

    # Timing
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0

    # Output
    stdout: str = ""
    stderr: str = ""
    log_file: Path | None = None

    # Image info
    image_used: str | None = None
    image_load_time: float = 0.0

    # Error details
    error_message: str | None = None

    @property
    def success(self) -> bool:
        return self.status == JobStatus.PASSED

    @property
    def duration_display(self) -> str:
        """Human-readable duration."""
        if self.duration_seconds < 60:
            return f"{self.duration_seconds:.1f}s"
        minutes = int(self.duration_seconds // 60)
        seconds = self.duration_seconds % 60
        return f"{minutes}m {seconds:.0f}s"

    @property
    def status_icon(self) -> str:
        icons = {
            JobStatus.PENDING: "◌",
            JobStatus.PREPARING: "⟳",
            JobStatus.RUNNING: "●",
            JobStatus.PASSED: "✓",
            JobStatus.FAILED: "✗",
            JobStatus.TIMEOUT: "⏱",
            JobStatus.ERROR: "⚠",
            JobStatus.CANCELLED: "⊘",
            JobStatus.SKIPPED: "⊖",
        }
        return icons.get(self.status, "?")

    def summary_line(self) -> str:
        return (
            f"[{self.status_icon}] {self.matrix_name:40s} "
            f"{self.status.value:10s} {self.duration_display:>8s}"
        )


@dataclass
class ActCommand:
    """Builder for ``act`` CLI command.

    Constructs the full command-line invocation for act.
    """

    # Required
    workflow_file: Path
    job_id: str

    # Matrix filtering
    matrix_filters: dict[str, str] = field(default_factory=dict)

    # Image mapping
    runner_mappings: dict[str, str] = field(default_factory=dict)

    # Execution options
    pull: bool = False
    offline: bool = False  # Online by default (requires GitHub token)
    privileged: bool = True
    rm: bool = True
    dryrun: bool = False
    verbose: bool = False

    # Environment
    env: dict[str, str] = field(default_factory=dict)
    # Secrets passed to act via --secret-file (never as argv values).
    # All keys and values must be str (POSIX subprocess.Popen requirement).
    secrets: dict[str, str] = field(default_factory=dict)
    env_file: Path | None = None
    # Temp file for --secret-file; set by JobExecutor._execute_process only.
    secret_file: Path | None = None
    _executor_owned_secret_file: bool = field(default=False, repr=False)

    # Event
    event_file: Path | None = None

    # Container
    container_architecture: str | None = None

    # Action cache (per-job path to avoid parallel races in ~/.cache/act)
    action_cache_path: Path | None = None

    # Phase 2: bind mounts for ccache/boost/cmake (act --container-options "-v ...")
    container_options: str | None = None

    # Working directory
    workdir: Path | None = None

    # Binary name (set by executor)
    act_binary: str = "act"

    # Parsed act version for feature-gating; None means unknown (flags are always emitted)
    act_version: tuple[int, int, int] | None = None

    def build(self) -> list[str]:
        """Build complete act command as argument list."""
        cmd: list[str] = [self.act_binary]

        # Workflow file
        cmd.extend(["-W", str(self.workflow_file)])

        # Target job
        cmd.extend(["-j", self.job_id])

        # Matrix filters
        for key, value in self.matrix_filters.items():
            cmd.extend(["--matrix", f"{key}:{value}"])

        # Runner-to-image mappings
        for runner, image in self.runner_mappings.items():
            cmd.extend(["-P", f"{runner}={image}"])

        # Pull control
        if not self.pull:
            cmd.append("--pull=false")

        # Offline mode
        if self.offline:
            cmd.append("--action-offline-mode")

        # Privileged
        if self.privileged:
            cmd.append("--privileged")

        # Auto-remove containers
        if self.rm:
            cmd.append("--rm")

        # Dry run
        if self.dryrun:
            cmd.append("--dryrun")

        # Verbose
        if self.verbose:
            cmd.append("-v")

        # Environment variables
        for key, value in self.env.items():
            cmd.extend(["--env", f"{key}={value}"])

        # Env file
        if self.env_file:
            cmd.extend(["--env-file", str(self.env_file)])

        # Secrets file (path only on argv; values stay in the temp file)
        if self.secret_file:
            cmd.extend(["--secret-file", str(self.secret_file)])

        # Event payload
        if self.event_file:
            cmd.extend(["-e", str(self.event_file)])

        # Container architecture
        if self.container_architecture:
            cmd.extend(["--container-architecture", self.container_architecture])

        # Per-job action cache (avoids parallel jobs corrupting shared ~/.cache/act)
        # Requires act >= 0.2.47
        if self.action_cache_path:
            if self.act_version is None or self.act_version >= (0, 2, 47):
                cmd.extend(["--action-cache-path", str(self.action_cache_path)])
            else:
                logger.warning(
                    "--action-cache-path requires act >= 0.2.47 (found %s.%s.%s); skipping flag",
                    *self.act_version,
                )

        # Phase 2: bind mounts for build/boost/cmake caches
        # Requires act >= 0.2.35
        if self.container_options:
            if self.act_version is None or self.act_version >= (0, 2, 35):
                cmd.extend(["--container-options", self.container_options])
            else:
                logger.warning(
                    "--container-options requires act >= 0.2.35 (found %s.%s.%s); skipping flag",
                    *self.act_version,
                )

        return cmd

    def display(self) -> str:
        """Human-readable command string (secret values are not included in argv)."""
        return " ".join(self.build())

    def __str__(self) -> str:
        return self.display()


# =====================================================================
# JobExecutor
# =====================================================================


class JobExecutor:
    """Execute individual CI jobs using ``act``.

    Handles the complete lifecycle of a job execution:

    1. Verify prerequisites (``act``, Docker)
    2. Build ``act`` command from :class:`ActCommand`
    3. Run with real-time output capture
    4. Parse exit code into :class:`JobResult`
    5. Clean up temp files

    Usage::

        executor = JobExecutor(
            logs_dir=Path("~/.localci/logs"),
        )
        result = executor.run(cmd, matrix_entry_name="GCC 15: C++20")
    """

    def __init__(
        self,
        logs_dir: Path = _DEFAULT_LOGS_DIR,
    ) -> None:
        self.logs_dir = Path(logs_dir).expanduser()
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # On Windows, choco installs the binary as `act-cli.exe`
        # On Linux/macOS, it's `act`
        self._act_path: str | None = self._find_act_binary()
        self._act_version: tuple[int, int, int] | None = None

    @staticmethod
    def _find_act_binary() -> str | None:
        """Find the act binary, checking both 'act' and 'act-cli' (Windows).

        On Windows, Chocolatey installs act as `act-cli.exe`.
        On Linux/macOS, it's `act`.
        """
        # Try 'act' first (Linux/macOS, or manual Windows install)
        act_path = shutil.which("act")
        if act_path:
            return act_path

        # Try 'act-cli' (Windows Chocolatey package)
        if sys.platform == "win32":
            act_cli_path = shutil.which("act-cli")
            if act_cli_path:
                return act_cli_path

        return None

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def has_act(self) -> bool:
        """``True`` when the ``act`` binary is on ``PATH``."""
        return self._act_path is not None

    # -----------------------------------------------------------------
    # Preflight checks
    # -----------------------------------------------------------------

    def check_act(self) -> str:
        """Verify ``act`` is installed and return its version string.

        Raises :class:`ActNotFoundError` if not found.
        Also parses and caches the version tuple for feature-gating.
        """
        if not self._act_path:
            raise ActNotFoundError()

        result = subprocess.run(
            [self._act_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = result.stdout.strip()
        logger.info("act found: %s", version)
        import re

        m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", version)
        if m:
            self._act_version = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return version

    @property
    def act_version_tuple(self) -> tuple[int, int, int] | None:
        """Parsed act version as (major, minor, patch), or None if not yet checked."""
        return self._act_version

    def check_docker(self) -> None:
        """Verify Docker daemon is accessible.

        Raises :class:`DockerNotAvailableError` if Docker is missing
        or the daemon is not running.
        """
        docker_path = shutil.which("docker")
        if not docker_path:
            raise DockerNotAvailableError("Docker is not installed")

        result = subprocess.run(
            [docker_path, "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise DockerNotAvailableError("Docker daemon is not running")

    # -----------------------------------------------------------------
    # Main execution
    # -----------------------------------------------------------------

    def run(
        self,
        cmd: ActCommand,
        matrix_index: int = 0,
        matrix_name: str = "",
        timeout: int = 3600,
        stream_output: bool = True,
        on_output: Callable[[str], None] | None = None,
    ) -> JobResult:
        """Execute a single job.

        Parameters
        ----------
        cmd:
            Fully constructed :class:`ActCommand`.
        matrix_index:
            Index of the matrix entry being run.
        matrix_name:
            Human-readable name (e.g. ``"GCC 15: C++20"``).
        timeout:
            Maximum execution time in seconds.
        stream_output:
            Whether to print output lines to the console in real-time.
        on_output:
            Optional callback invoked for each output line.

        Returns
        -------
        JobResult
            Complete execution result with status, output, and timing.
        """
        result = JobResult(
            job_id=cmd.job_id,
            matrix_index=matrix_index,
            matrix_name=matrix_name,
        )

        act_cmd: ActCommand | None = cmd

        # Set the correct binary name for this platform
        if self._act_path:
            cmd.act_binary = self._act_path

        try:
            # Preflight
            self.check_act()
            self.check_docker()

            logger.info("Command: %s", cmd.display())

            # Dry-run short-circuit
            if cmd.dryrun:
                result.status = JobStatus.SKIPPED
                result.stdout = f"DRY RUN: {cmd.display()}"
                return result

            # Prepare log file
            log_file = self._get_log_path(matrix_name)
            result.log_file = log_file

            # Execute
            result.status = JobStatus.RUNNING
            result.started_at = datetime.now()

            exit_code, stdout, stderr = self._execute_process(
                act_cmd=cmd,
                timeout=timeout,
                log_file=log_file,
                stream_output=stream_output,
                on_output=on_output,
            )

            result.finished_at = datetime.now()
            result.duration_seconds = (
                result.finished_at - result.started_at
            ).total_seconds()
            result.exit_code = exit_code
            result.stdout = stdout
            result.stderr = stderr

            # Determine status from exit code
            if exit_code == 0:
                result.status = JobStatus.PASSED
            else:
                result.status = JobStatus.FAILED
                result.error_message = self._extract_error(stderr or stdout)

        except subprocess.TimeoutExpired:
            result.status = JobStatus.TIMEOUT
            result.finished_at = datetime.now()
            result.duration_seconds = float(timeout)
            result.error_message = f"Job timed out after {timeout}s"
            logger.error("Timeout: %s", matrix_name)

        except KeyboardInterrupt:
            result.status = JobStatus.CANCELLED
            result.finished_at = datetime.now()
            if result.started_at:
                result.duration_seconds = (
                    result.finished_at - result.started_at
                ).total_seconds()
            result.error_message = "Cancelled by user"
            logger.info("Cancelled: %s", matrix_name)

        except (ActNotFoundError, DockerNotAvailableError) as exc:
            result.status = JobStatus.ERROR
            result.error_message = str(exc)
            logger.error("Preflight failed: %s", exc)

        except Exception as exc:
            result.status = JobStatus.ERROR
            result.finished_at = datetime.now()
            result.error_message = str(exc)
            logger.exception("Error executing %s: %s", matrix_name, exc)

        finally:
            self._cleanup_temp_files(act_cmd)

        logger.info(result.summary_line())
        return result

    # -----------------------------------------------------------------
    # Process execution
    # -----------------------------------------------------------------

    def _execute_process(
        self,
        act_cmd: ActCommand,
        timeout: int,
        log_file: Path,
        stream_output: bool,
        on_output: Callable[[str], None] | None,
    ) -> tuple[int, str, str]:
        """Execute ``act`` process with output capture and streaming.

        Uses :meth:`ActCommand.display` for the log header. Secret values are
        written to a ``0600`` temp file and passed via ``--secret-file`` (path
        only on argv); :attr:`ActCommand.secrets` is also injected into the
        subprocess environment for act's GitHub authentication.

        Returns ``(exit_code, stdout, stderr)``.
        """
        workdir = act_cmd.workdir or Path(".")
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        log_lock = threading.Lock()

        if act_cmd.secrets and not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in act_cmd.secrets.items()
        ):
            raise TypeError(
                "ActCommand.secrets must contain only str keys and str values"
            )

        if act_cmd.secrets:
            fd, secret_path = tempfile.mkstemp(
                prefix="localci-secrets-",
                suffix=".env",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as secret_f:
                    for key, value in act_cmd.secrets.items():
                        secret_f.write(_format_secret_file_line(key, value))
            except Exception:
                with suppress(OSError):
                    os.close(fd)
                with suppress(OSError):
                    Path(secret_path).unlink()
                raise
            act_cmd.secret_file = Path(secret_path)
            act_cmd._executor_owned_secret_file = True

        cmd = act_cmd.build()

        with open(log_file, "w", encoding="utf-8") as log_f:
            # Write header (secret values are not on argv)
            log_f.write("# LocalCI Job Log\n")
            log_f.write(f"# Command: {act_cmd.display()}\n")
            log_f.write(f"# Started: {datetime.now().isoformat()}\n")
            log_f.write(f"# {'=' * 60}\n\n")

            env = dict(os.environ)
            env.update(act_cmd.secrets)

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(workdir),
                bufsize=1,  # Line-buffered
                env=env,
            )

            def read_stream(stream: IO[str], lines: list[str], prefix: str) -> None:
                for line in stream:
                    lines.append(line)
                    with log_lock:
                        log_f.write(line)
                        log_f.flush()
                    if stream_output:
                        print(f"{prefix}{line}", end="")
                    if on_output:
                        on_output(line.rstrip())

            stdout_thread = threading.Thread(
                target=read_stream,
                args=(process.stdout, stdout_lines, ""),
            )
            stderr_thread = threading.Thread(
                target=read_stream,
                args=(process.stderr, stderr_lines, "[ERR] "),
            )

            stdout_thread.start()
            stderr_thread.start()

            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise

            stdout_thread.join()
            stderr_thread.join()

            # Write footer
            log_f.write(f"\n# {'=' * 60}\n")
            log_f.write(f"# Finished: {datetime.now().isoformat()}\n")
            log_f.write(f"# Exit code: {process.returncode}\n")

        return (
            process.returncode,
            "".join(stdout_lines),
            "".join(stderr_lines),
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _get_log_path(self, matrix_name: str) -> Path:
        """Generate a log file path for a job."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = (
            matrix_name.replace(" ", "_")
            .replace(":", "")
            .replace("/", "-")
            .replace("(", "")
            .replace(")", "")
        )
        filename = f"{timestamp}_{safe_name}.log"
        return self.logs_dir / filename

    @staticmethod
    def _line_indicates_error(line: str) -> bool:
        """Return True if *line* looks like a failed-job error summary."""
        lower = line.lower()
        if any(kw in lower for kw in _ERROR_EXTRACT_KEYWORDS):
            return True
        if any(kw in lower for kw in AUTH_ERROR_EXTRACT_KEYWORDS):
            return True
        return _HTTP_STATUS_PATTERN.search(line) is not None

    @staticmethod
    def _extract_error(output: str, max_lines: int = 10) -> str:
        """Extract error summary from output."""
        lines = output.strip().split("\n")

        error_lines: list[str] = []
        for line in lines:
            if JobExecutor._line_indicates_error(line):
                error_lines.append(line.strip())

        if error_lines:
            return "\n".join(error_lines[:max_lines])

        # Fallback: last N lines
        return "\n".join(lines[-max_lines:])

    @staticmethod
    def _cleanup_temp_files(cmd: ActCommand | None) -> None:
        """Remove temporary files created during execution."""
        if cmd and cmd.event_file and cmd.event_file.exists():
            with suppress(OSError):
                cmd.event_file.unlink()
        if (
            cmd
            and cmd._executor_owned_secret_file
            and cmd.secret_file
            and cmd.secret_file.exists()
        ):
            with suppress(OSError):
                cmd.secret_file.unlink()
