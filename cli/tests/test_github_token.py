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
    warn_sentinel_github_token,
)
from localci.utils.output import configure_console

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

    def test_warn_sentinel_emits_rich_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        warn_sentinel_github_token(SENTINEL_GITHUB_TOKEN)
        out = capsys.readouterr().out
        assert out.lstrip().startswith("!")
        assert "No GitHub token provided" in out

    def test_warn_sentinel_visible_when_console_quiet(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console(quiet=True)
        warn_sentinel_github_token(SENTINEL_GITHUB_TOKEN)
        out = capsys.readouterr().out
        assert "No GitHub token provided" in out
        configure_console(quiet=False)


class TestAuthErrorExtractKeywords:
    def test_auth_keywords_registered(self) -> None:
        assert AUTH_ERROR_EXTRACT_KEYWORDS == (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "rate limit",
        )


class TestExtractErrorAuthKeywords:
    @pytest.mark.parametrize(
        "line",
        [
            "Error: HTTP 401 Unauthorized",
            "received HTTP status: 403",
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
