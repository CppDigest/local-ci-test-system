"""End-to-end secrets non-leak invariant tests (is-3).

Proves configured secret plaintext never appears on capture surfaces
(stdout, stderr, log file) or in persisted ExecutionSummary JSON fields
(error_message, log_file path, etc.). to_dict() does not serialize stdout/stderr.

Env delivery (executor.py:679, asserted in test_secrets_injected_into_subprocess_env)
is out of scope for this invariant: we scan capture surfaces Local CI writes
(logs, summaries, display), not child-process env dumps. /proc/<pid>/environ
visibility is the accepted tradeoff noted in executor comments.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from localci.core.executor import ActCommand, JobExecutor, JobStatus
from localci.core.results import ExecutionSummary

from .test_executor import (
    _assert_file_has_no_secret_plaintext,
    _assert_text_has_no_secret_plaintext,
)

INVARIANT_SECRET = "localci-invariant-secret-7f3a9c2e"

# Per-result keys ExecutionSummary.to_dict() serializes (no stdout/stderr).
_SUMMARY_RESULT_JSON_KEYS = frozenset(
    {
        "job_id",
        "matrix_index",
        "matrix_name",
        "status",
        "exit_code",
        "duration",
        "image_used",
        "log_file",
        "error_message",
    }
)


def _mock_popen_factory(
    *,
    returncode: int,
    stdout_lines: list[str],
    stderr_lines: list[str],
) -> MagicMock:
    mock_process = MagicMock()
    mock_process.stdout = stdout_lines
    mock_process.stderr = stderr_lines
    mock_process.returncode = returncode
    return mock_process


class TestSecretsNonLeakInvariant:
    @patch("shutil.which")
    def test_execute_process_capture_surfaces_contain_no_secret_plaintext(
        self, mock_which, tmp_path
    ):
        """Stdout, stderr, and on-disk log must not contain configured secret plaintext."""
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=tmp_path / "logs")
        act_cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            secrets={"GITHUB_TOKEN": INVARIANT_SECRET},
            workdir=tmp_path,
        )
        log_file = tmp_path / "job.log"

        # Benign mock output: Local CI does not redact streams. If stdout/stderr
        # contained INVARIANT_SECRET, the assertions below should fail.
        mock_process = _mock_popen_factory(
            returncode=0,
            stdout_lines=["Building project...\n", "compile ok\n"],
            stderr_lines=["warning: deprecated flag\n"],
        )

        with patch(
            "localci.core.executor.subprocess.Popen",
            return_value=mock_process,
        ):
            exit_code, stdout, stderr = executor._execute_process(
                act_cmd,
                timeout=10,
                log_file=log_file,
                stream_output=False,
                on_output=None,
            )

        assert exit_code == 0
        _assert_text_has_no_secret_plaintext(stdout, INVARIANT_SECRET)
        _assert_text_has_no_secret_plaintext(stderr, INVARIANT_SECRET)
        _assert_file_has_no_secret_plaintext(log_file, INVARIANT_SECRET)
        _assert_text_has_no_secret_plaintext(act_cmd.display(), INVARIANT_SECRET)

    @patch("shutil.which")
    def test_run_success_job_result_capture_surfaces_contain_no_secret_plaintext(
        self, mock_which, tmp_path
    ):
        """JobResult stdout, stderr, and log_file must not contain secret plaintext."""
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=tmp_path / "logs")
        act_cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            secrets={"GITHUB_TOKEN": INVARIANT_SECRET},
            workdir=tmp_path,
        )

        mock_process = _mock_popen_factory(
            returncode=0,
            stdout_lines=["Building project...\n", "compile ok\n"],
            stderr_lines=["warning: deprecated flag\n"],
        )

        with (
            patch.object(executor, "check_act", return_value="act 0.2.68"),
            patch.object(executor, "check_docker"),
            patch(
                "localci.core.executor.subprocess.Popen",
                return_value=mock_process,
            ),
        ):
            result = executor.run(
                act_cmd,
                matrix_index=0,
                matrix_name="GCC 15: C++20",
            )

        assert result.status == JobStatus.PASSED
        assert result.log_file is not None
        _assert_text_has_no_secret_plaintext(result.stdout, INVARIANT_SECRET)
        _assert_text_has_no_secret_plaintext(result.stderr, INVARIANT_SECRET)
        _assert_file_has_no_secret_plaintext(result.log_file, INVARIANT_SECRET)

    @patch("shutil.which")
    def test_execution_summary_save_contains_no_secret_plaintext(
        self, mock_which, tmp_path
    ):
        """Failed-job error_message serialized to JSON must not contain secret plaintext.

        ExecutionSummary.to_dict() omits stdout/stderr; this exercises the
        failure path where error_message is populated via _extract_error().
        """
        mock_which.return_value = "/usr/bin/act"
        executor = JobExecutor(logs_dir=tmp_path / "logs")
        act_cmd = ActCommand(
            workflow_file=Path("ci.yml"),
            job_id="build",
            secrets={"GITHUB_TOKEN": INVARIANT_SECRET},
            workdir=tmp_path,
        )

        mock_process = _mock_popen_factory(
            returncode=1,
            stdout_lines=["act: starting job\n"],
            stderr_lines=["Error: act job failed\n"],
        )

        with (
            patch.object(executor, "check_act", return_value="act 0.2.68"),
            patch.object(executor, "check_docker"),
            patch(
                "localci.core.executor.subprocess.Popen",
                return_value=mock_process,
            ),
        ):
            result = executor.run(
                act_cmd,
                matrix_index=0,
                matrix_name="GCC 15: C++20",
            )

        assert result.status == JobStatus.FAILED
        assert result.error_message
        assert "Error: act job failed" in result.error_message
        _assert_text_has_no_secret_plaintext(result.error_message, INVARIANT_SECRET)
        assert result.log_file is not None
        _assert_file_has_no_secret_plaintext(result.log_file, INVARIANT_SECRET)

        summary = ExecutionSummary(
            execution_id="exec-non-leak",
            started_at=datetime(2026, 7, 28, 10, 0, 0),
        )
        summary.results.append(result)
        serialized = summary.to_dict()["results"][0]
        assert set(serialized.keys()) == _SUMMARY_RESULT_JSON_KEYS
        summary_path = tmp_path / "results.json"
        summary.save(summary_path)

        _assert_file_has_no_secret_plaintext(summary_path, INVARIANT_SECRET)
