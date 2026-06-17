"""Godotenv-compatible secret file formatting for act ``--secret-file``."""

from __future__ import annotations

_INVALID_SECRET_KEY_CHARS = frozenset("=\n\r\0")


def validate_secret_key(key: str) -> None:
    """Reject secret keys that would corrupt act's godotenv secret file."""
    if any(char in key for char in _INVALID_SECRET_KEY_CHARS):
        raise ValueError(f"Invalid secret key: {key!r}")


def format_secret_file_line(key: str, value: str) -> str:
    """Format one KEY=VALUE line for act's godotenv ``--secret-file`` parser."""
    validate_secret_key(key)
    if "\0" in value:
        raise ValueError(f"Invalid secret value for key {key!r}")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'{key}="{escaped}"\n'
