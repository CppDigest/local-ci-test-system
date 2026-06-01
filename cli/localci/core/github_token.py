"""GitHub token resolution for act and workflow action downloads."""

from __future__ import annotations

import os

SENTINEL_GITHUB_TOKEN = "local-ci-token"


def resolve_github_token(cli_token: str | None) -> str:
    """Return CLI token, ``GITHUB_TOKEN`` env, or the local-ci sentinel."""
    if cli_token:
        return cli_token
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token
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
