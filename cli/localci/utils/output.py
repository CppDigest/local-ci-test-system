"""Rich console helpers for consistent terminal output.

All CLI commands should use these helpers rather than ``print()`` so that
colour/style toggles (``--no-color``, ``--quiet``) are respected globally.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Custom theme
# ---------------------------------------------------------------------------

LOCALCI_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "header": "bold magenta",
        "muted": "dim",
        "key": "bold cyan",
        "value": "white",
    }
)

# Module-level consoles (reconfigured by ``configure_console``).
console = Console(theme=LOCALCI_THEME, no_color=False)
# High-severity messages (e.g. missing GitHub token) bypass ``--quiet``.
# Bind to sys.stdout so each print uses the current stream (pytest, CliRunner).
_important_console = Console(
    theme=LOCALCI_THEME, file=sys.stdout, no_color=False, quiet=False
)


def configure_console(*, no_color: bool = False, quiet: bool = False) -> None:
    """Reconfigure the global *console* based on CLI flags."""
    global console, _important_console
    console = Console(
        theme=LOCALCI_THEME,
        no_color=no_color,
        quiet=quiet,
    )
    _important_console = Console(
        theme=LOCALCI_THEME,
        file=sys.stdout,
        no_color=no_color,
        quiet=False,
    )


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def print_header(title: str) -> None:
    """Print a styled panel header."""
    console.print(Panel(title, style="header", expand=True))


def print_success(message: str) -> None:
    console.print(f"[success]✓[/success] {message}")


def print_error(message: str) -> None:
    console.print(f"[error]✗[/error] {message}")


def print_warning(message: str) -> None:
    console.print(f"[warning]![/warning] {message}")


def print_important_warning(message: str) -> None:
    """Print a warning that is still shown when ``--quiet`` is set."""
    _important_console.print(f"[warning]![/warning] {message}")


def print_info(message: str) -> None:
    console.print(f"[info]ℹ[/info] {message}")


def print_key_value(key: str, value: str) -> None:
    """Print a key: value pair with consistent styling."""
    console.print(f"  [key]{key}:[/key] [value]{value}[/value]")


def make_table(*columns: str, title: str | None = None) -> Table:
    """Create a consistently-styled Rich table.

    Parameters
    ----------
    *columns:
        Column header strings.
    title:
        Optional table title.
    """
    table = Table(title=title, show_header=True, header_style="bold cyan")
    for col in columns:
        table.add_column(col)
    return table
