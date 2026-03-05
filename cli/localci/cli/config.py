"""``localci config`` command group.

View, initialise, and modify the ``.localci.yml`` configuration file.
"""

from __future__ import annotations

from pathlib import Path

import click
import yaml

from localci.core.config import (
    LocalCIConfig,
    default_config_yaml,
    find_config_file,
    _stringify_paths,
)
from localci.utils.output import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)


@click.group()
@click.pass_context
def config(ctx: click.Context) -> None:
    """Manage configuration."""


# ---------------------------------------------------------------------------
# localci config show
# ---------------------------------------------------------------------------


@config.command("show")
@click.option("--effective", is_flag=True, help="Show effective (merged) configuration.")
@click.pass_context
def config_show(ctx: click.Context, effective: bool) -> None:
    """Show current configuration."""
    cfg: LocalCIConfig = ctx.obj["config"]

    if effective:
        print_info("Effective (merged) configuration:")
    else:
        config_path = find_config_file()
        if config_path:
            print_info(f"Configuration file: {config_path}")
        else:
            print_warning("No config file found – showing defaults")

    # Dump the active config as YAML.
    data = cfg.model_dump(mode="json")
    _stringify_paths(data)
    console.print(yaml.dump(data, default_flow_style=False, sort_keys=False))


# ---------------------------------------------------------------------------
# localci config init
# ---------------------------------------------------------------------------


@config.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config file.")
@click.pass_context
def config_init(ctx: click.Context, force: bool) -> None:
    """Create a default .localci.yml in the current directory."""
    target = Path.cwd() / ".localci.yml"

    if target.exists() and not force:
        print_error(
            f"Config file already exists: {target}\n"
            "  Use --force to overwrite."
        )
        ctx.exit(1)
        return

    content = default_config_yaml()
    target.write_text(content, encoding="utf-8")
    print_success(f"Created config file: {target}")


# ---------------------------------------------------------------------------
# localci config set
# ---------------------------------------------------------------------------


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str) -> None:
    """Set a configuration value (dot-notation key).

    Example: localci config set parallel.max_jobs 8
    """
    config_path = find_config_file()
    if config_path is None:
        print_error("No config file found. Run 'localci config init' first.")
        ctx.exit(1)
        return

    with open(config_path, "r", encoding="utf-8") as fh:
        data: dict = yaml.safe_load(fh) or {}

    # Navigate dot-notation key.
    keys = key.split(".")
    target = data
    for k in keys[:-1]:
        if k not in target or not isinstance(target[k], dict):
            target[k] = {}
        target = target[k]

    # Attempt type coercion for common cases.
    target[keys[-1]] = _coerce_value(value)

    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

    print_success(f"Set {key} = {value}")


# ---------------------------------------------------------------------------
# localci config get
# ---------------------------------------------------------------------------


@config.command("get")
@click.argument("key")
@click.pass_context
def config_get(ctx: click.Context, key: str) -> None:
    """Get a configuration value (dot-notation key).

    Example: localci config get parallel.max_jobs
    """
    cfg: LocalCIConfig = ctx.obj["config"]
    data = cfg.model_dump(mode="json")

    keys = key.split(".")
    current = data
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            print_error(f"Key not found: {key}")
            ctx.exit(1)
            return

    console.print(current)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_value(raw: str) -> str | int | float | bool:
    """Best-effort coercion of a CLI string value to a native Python type."""
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
