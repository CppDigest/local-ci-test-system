"""Root CLI group and entry point.

This module defines the top-level ``localci`` command group, wires in
global options (``--config``, ``--verbose``, ``--quiet``, ``--no-color``),
and registers every sub-command.
"""

from __future__ import annotations

import logging
from typing import Any

import click

from localci import __version__
from localci.core.config import load_config
from localci.errors import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigIOError,
    ConfigValidationError,
    LocalCIError,
)
from localci.utils.crash import crash_log_display_path, log_crash
from localci.utils.output import configure_console, print_error

# ---------------------------------------------------------------------------
# Catch-all exception handling
# ---------------------------------------------------------------------------


class CatchAllGroup(click.Group):
    """Click group that catches unhandled exceptions at the CLI entry point."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except (click.exceptions.Exit, click.ClickException):
            raise
        except LocalCIError as exc:
            if _is_debug(ctx):
                raise
            print_error(str(exc))
            ctx.exit(1)
        except Exception as exc:
            if _is_debug(ctx):
                raise
            log_path = log_crash(exc)
            _print_unhandled_error(exc, log_path is not None)
            ctx.exit(2)


def _is_debug(ctx: click.Context) -> bool:
    """Return True when ``--debug`` was passed on the CLI."""
    obj = ctx.obj
    return bool(obj is not None and obj.get("debug"))


def _print_unhandled_error(exc: BaseException, crash_log_written: bool) -> None:
    """Print a user-friendly message for an unhandled internal error."""
    if crash_log_written:
        attach_hint = (
            f"Please file a bug report and attach {crash_log_display_path()} "
            "(contains the full traceback)."
        )
    else:
        attach_hint = (
            "Please file a bug report; the full traceback was printed to stderr "
            "(crash log could not be written)."
        )
    messages = [
        "An unexpected internal error occurred.",
        f"{type(exc).__name__}: {exc}",
        attach_hint,
    ]
    try:
        for msg in messages:
            print_error(msg)
    except Exception:
        for msg in messages:
            click.echo(msg, err=True)


# ---------------------------------------------------------------------------
# Root CLI group
# ---------------------------------------------------------------------------


@click.group(
    cls=CatchAllGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to config file (.localci.yml).",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-essential output.")
@click.option("--no-color", is_flag=True, help="Disable coloured output.")
@click.option(
    "--debug",
    is_flag=True,
    help="Show full tracebacks instead of friendly errors.",
)
@click.version_option(version=__version__, prog_name="localci")
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: str | None,
    verbose: bool,
    quiet: bool,
    no_color: bool,
    debug: bool,
) -> None:
    """Local CI - Run GitHub Actions workflows locally."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug

    # Configure Rich console early so all downstream output respects flags.
    configure_console(no_color=no_color, quiet=quiet)

    # Set up logging level.
    log_level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(name)s %(levelname)s: %(message)s",
    )

    # Load configuration – surface any validation errors immediately.
    try:
        cfg = load_config(config_path)
    except ConfigFileNotFoundError as exc:
        print_error(str(exc))
        ctx.exit(1)
        return
    except ConfigIOError as exc:
        print_error(f"Cannot read config file {exc.path}: {exc.cause}")
        ctx.exit(1)
        return
    except ConfigValidationError as exc:
        location = f" {exc.path}" if exc.path else ""
        print_error(f"Invalid config{location}: {exc.cause}")
        ctx.exit(1)
        return
    except ConfigError as exc:
        print_error(str(exc))
        ctx.exit(1)
        return

    # Stash shared state for sub-commands.
    ctx.obj["config"] = cfg
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["no_color"] = no_color


# ---------------------------------------------------------------------------
# Register sub-commands
# ---------------------------------------------------------------------------

from localci.cli.analyze import analyze  # noqa: E402
from localci.cli.cache import cache_cmd  # noqa: E402
from localci.cli.config import config  # noqa: E402
from localci.cli.images import images  # noqa: E402
from localci.cli.list import list_cmd  # noqa: E402
from localci.cli.logs import logs  # noqa: E402
from localci.cli.run import run  # noqa: E402
from localci.cli.status import status  # noqa: E402

cli.add_command(analyze)
cli.add_command(cache_cmd, name="cache")
cli.add_command(list_cmd, name="list")
cli.add_command(run)
cli.add_command(status)
cli.add_command(logs)
cli.add_command(images)
cli.add_command(config)
