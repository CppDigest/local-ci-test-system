"""Root CLI group and entry point.

This module defines the top-level ``localci`` command group, wires in
global options (``--config``, ``--verbose``, ``--quiet``, ``--no-color``),
and registers every sub-command.
"""

from __future__ import annotations

import logging
import sys

import click

from localci import __version__
from localci.core.config import load_config
from localci.errors import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigIOError,
    ConfigValidationError,
)
from localci.utils.output import configure_console, console, print_error

# ---------------------------------------------------------------------------
# Root CLI group
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to config file (.localci.yml).",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose/debug output.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-essential output.")
@click.option("--no-color", is_flag=True, help="Disable coloured output.")
@click.version_option(version=__version__, prog_name="localci")
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: str | None,
    verbose: bool,
    quiet: bool,
    no_color: bool,
) -> None:
    """Local CI - Run GitHub Actions workflows locally."""
    ctx.ensure_object(dict)

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
