"""Tests for GitHub token resolution and sentinel warnings."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from localci.cli.main import cli
from localci.core.executor import AUTH_ERROR_EXTRACT_KEYWORDS, JobExecutor
from localci.core.github_token import (
    SENTINEL_GITHUB_TOKEN,
    format_sentinel_github_token_warning,
    is_sentinel_github_token,
    resolve_github_token,
)
runner = CliRunner()
SAMPLE_WORKFLOW = str(Path(__file__).parent / "fixtures" / "sample_workflow.yml")


class TestResolveGithubToken:
    def test_cli_token_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        assert resolve_github_token("cli-token") == "cli-token"

    def test_env_token_when_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        assert resolve_github_token(None) == "env-token"

    def test_sentinel_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert resolve_github_token(None) == SENTINEL_GITHUB_TOKEN

    def test_blank_cli_token_falls_back_to_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert resolve_github_token("   ") == SENTINEL_GITHUB_TOKEN

    def test_blank_env_token_falls_back_to_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "   ")
        assert resolve_github_token(None) == SENTINEL_GITHUB_TOKEN

    def test_env_token_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "  env-token  ")
        assert resolve_github_token(None) == "env-token"

    def test_is_sentinel_detects_placeholder(self) -> None:
        assert is_sentinel_github_token(SENTINEL_GITHUB_TOKEN)
        assert not is_sentinel_github_token("ghp_real")

    def test_warning_message_documents_remediation(self) -> None:
        msg = format_sentinel_github_token_warning()
        assert "GITHUB_TOKEN" in msg
        assert "--github-token" in msg
        assert "--offline" in msg
        assert "401" in msg

class TestAuthErrorExtractKeywords:
    def test_auth_keywords_registered(self) -> None:
        assert AUTH_ERROR_EXTRACT_KEYWORDS == (
            "unauthorized",
            "forbidden",
            "rate limit",
        )


class TestExtractErrorAuthKeywords:
    @pytest.mark.parametrize(
        "line",
        [
            "Error: HTTP 401 Unauthorized",
            "authentication required: unauthorized",
            "403 Forbidden: resource not accessible",
            "received HTTP status: 403",
            "API rate limit exceeded for user",
        ],
    )
    def test_extract_error_matches_auth_keywords(self, line: str) -> None:
        output = textwrap.dedent(f"""\
            Downloading action
            {line}
            cleanup
        """)
        extracted = JobExecutor._extract_error(output)
        assert line.strip() in extracted


class TestRunSentinelWarning:
    def test_dry_run_warns_when_no_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(
            cli,
            ["run", "--workflow", SAMPLE_WORKFLOW, "--dry-run", "--platform", "linux"],
        )
        assert result.exit_code == 0, result.output
        assert "No GitHub token provided" in result.output
        assert "GITHUB_TOKEN" in result.output

    def test_dry_run_warns_when_no_token_and_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(
            cli,
            [
                "-q",
                "run",
                "--workflow",
                SAMPLE_WORKFLOW,
                "--dry-run",
                "--platform",
                "linux",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No GitHub token provided" in result.output

    def test_dry_run_no_warning_when_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(
            cli,
            [
                "run",
                "--workflow",
                SAMPLE_WORKFLOW,
                "--dry-run",
                "--platform",
                "linux",
                "--offline",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No GitHub token provided" not in result.output

    def test_dry_run_no_warning_with_cli_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(
            cli,
            [
                "run",
                "--workflow",
                SAMPLE_WORKFLOW,
                "--dry-run",
                "--platform",
                "linux",
                "--github-token",
                "ghp_test_token",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No GitHub token provided" not in result.output

    def test_early_exit_still_warns_when_no_jobs_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(
            cli,
            [
                "run",
                "--workflow",
                SAMPLE_WORKFLOW,
                "--dry-run",
                "--platform",
                "linux",
                "--job",
                "nonexistent-job-xyz",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No GitHub token provided" in result.output
        assert "No jobs match" in result.output
