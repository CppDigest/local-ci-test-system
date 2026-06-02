"""GitHub token resolution for act and workflow action downloads.

Part of the token slice of the Silent Failure Chain (T9 defaults + T10 error
extraction): missing tokens previously produced opaque act 401s with no
upfront warning.
"""

from __future__ import annotations

import os

from localci.utils.output import print_important_warning

SENTINEL_GITHUB_TOKEN = "local-ci-token"


def resolve_github_token(cli_token: str | None) -> str:
    """Return CLI token, ``GITHUB_TOKEN`` env, or the local-ci sentinel."""
    if cli_token is not None:
        stripped = cli_token.strip()
        if stripped:
            return stripped
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    return SENTINEL_GITHUB_TOKEN


def is_sentinel_github_token(token: str) -> bool:
    """True when *token* is the non-functional placeholder used for local runs."""
    return token == SENTINEL_GITHUB_TOKEN


def format_sentinel_github_token_warning() -> str:
    """User-facing warning when falling back to :data:`SENTINEL_GITHUB_TOKEN`."""
    return (
        "No GitHub token provided; using placeholder token for act. "
        "Action downloads may fail with HTTP 401. Set a real token via: "
        "export GITHUB_TOKEN=ghp_... , "
        "localci run --github-token ghp_... , "
        "or use --offline if actions are already cached."
    )


def warn_sentinel_github_token(token: str) -> None:
    """Emit a Rich console warning when *token* is :data:`SENTINEL_GITHUB_TOKEN`."""
    if is_sentinel_github_token(token):
        print_important_warning(format_sentinel_github_token_warning())
