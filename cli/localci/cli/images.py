"""``localci images`` command group.

Manage Docker images: list, inspect, build, clean, import, and export.
Uses the image registry (image-registry.yml) and two-mark matching from core.registry.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click
import yaml

from localci.core.config import find_config_file
from localci.core.registry import ImageRegistry
from localci.utils.docker import DockerManager
from localci.utils.output import (
    console,
    make_table,
    print_error,
    print_info,
    print_success,
    print_warning,
)


def _project_root() -> Path:
    """Project root for images/registry: directory containing .localci.yml, or cwd."""
    config_path = find_config_file(Path.cwd())
    if config_path is not None:
        return config_path.parent.resolve()
    return Path.cwd().resolve()


def _images_dir() -> Path:
    return _project_root() / "images" / "capy"


def _registry_file() -> Path:
    return _project_root() / "image-registry.yml"


def _get_registry(registry_path: Path | None = None) -> ImageRegistry:
    path = registry_path or _registry_file()
    if not path.exists():
        raise FileNotFoundError(f"Registry file not found: {path}")
    registry = ImageRegistry(path)
    registry.load()
    return registry


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


@click.group()
@click.pass_context
def images(ctx: click.Context) -> None:
    """Manage Docker images."""


# ---------------------------------------------------------------------------
# localci images list
# ---------------------------------------------------------------------------


@images.command("list")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
@click.option(
    "--registry",
    "-r",
    "registry_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Path to image-registry.yml.",
)
@click.pass_context
def images_list(ctx: click.Context, output_format: str, registry_path: Path | None) -> None:
    """List available images."""
    try:
        registry = _get_registry(registry_path)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc))
        ctx.exit(1)

    if output_format == "json":
        click.echo(json.dumps([e.to_dict() for e in registry.entries], indent=2))
        return

    table = make_table("Name", "Tag", "OS", "Arch", "Variants", title="Image Registry")
    for e in registry.entries:
        variants = ", ".join(e.variants) if e.variants else "-"
        table.add_row(
            e.name,
            e.docker_tag,
            e.os,
            e.architecture,
            variants,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# localci images info
# ---------------------------------------------------------------------------


@images.command("info")
@click.argument("image")
@click.option(
    "--registry",
    "-r",
    "registry_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Path to image-registry.yml.",
)
@click.pass_context
def images_info(ctx: click.Context, image: str, registry_path: Path | None) -> None:
    """Show detailed information about an image."""
    try:
        registry = _get_registry(registry_path)
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc))
        ctx.exit(1)

    match = registry.find_by_name(image)
    if not match:
        print_warning(f"Image not found in registry: {image}")
        ctx.exit(1)

    click.echo(yaml.safe_dump(match.to_dict(), sort_keys=False))


# ---------------------------------------------------------------------------
# localci images build
# ---------------------------------------------------------------------------


@images.command("build")
@click.option("--all", "build_all", is_flag=True, help="Build all missing images.")
@click.option("--force", is_flag=True, help="Rebuild even if image exists.")
@click.argument("image_names", nargs=-1)
@click.pass_context
def images_build(
    ctx: click.Context,
    build_all: bool,
    force: bool,
    image_names: tuple[str, ...],
) -> None:
    """Build Docker images.

    Specify one or more IMAGE_NAMES, or use --all to build every missing image.
    """
    if force:
        print_warning("--force is not yet implemented; proceeding without force logic.")

    images_dir = _images_dir()
    if not images_dir.exists():
        print_error(f"Images directory not found: {images_dir}")
        ctx.exit(1)

    build_all_script = images_dir / "build-all.sh"
    build_one_script = images_dir / "build-one.sh"

    if build_all:
        cmd = ["bash", str(build_all_script), "--save"]
        result = _run(cmd)
        if result.returncode != 0:
            print_error(result.stderr.strip() or "Build failed.")
            ctx.exit(result.returncode)
        print_success("Built all images.")
        return

    if image_names:
        for image in image_names:
            cmd = ["bash", str(build_one_script), image, "--save"]
            result = _run(cmd)
            if result.returncode != 0:
                print_error(result.stderr.strip() or f"Build failed for {image}.")
                ctx.exit(result.returncode)
            print_success(f"Built image: {image}")
        return

    print_info("No images specified. Use --all or provide image names.")


# ---------------------------------------------------------------------------
# localci images clean
# ---------------------------------------------------------------------------


def _parse_older_than(s: str) -> timedelta | None:
    """Parse --older-than value: e.g. 7d, 30d, 2w, 1m (m = 30 days)."""
    m = re.match(r"^(\d+)(d|w|m)$", s.strip().lower())
    if not m:
        return None
    num = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return timedelta(days=num)
    if unit == "w":
        return timedelta(weeks=num)
    if unit == "m":
        return timedelta(days=num * 30)
    return None


@images.command("clean")
@click.option("--older-than", type=str, default=None, help="Remove registry images not used since (e.g. 30d, 7d, 2w).")
@click.option("--unused", is_flag=True, help="Remove registry images with usage_count 0.")
@click.option("--all", "clean_all", is_flag=True, help="Remove all localci capy images (Docker + registry).")
@click.option("--dry-run", is_flag=True, help="Preview without removing.")
@click.option("--registry", "-r", "registry_path", type=click.Path(path_type=Path, exists=False), default=None, help="Path to image-registry.yml.")
@click.pass_context
def images_clean(
    ctx: click.Context,
    older_than: str | None,
    unused: bool,
    clean_all: bool,
    dry_run: bool,
    registry_path: Path | None,
) -> None:
    """Clean up Docker images and optionally registry / .tar files."""
    reg_path = registry_path or _registry_file()
    project_dir = _project_root()

    # Disk space management: --older-than and --unused (registry-based)
    if older_than or unused:
        if not reg_path.exists():
            print_error(f"Registry not found: {reg_path}. Cannot use --older-than/--unused.")
            ctx.exit(1)
        delta = None
        if older_than:
            delta = _parse_older_than(older_than)
            if not delta:
                print_error("--older-than must be like 7d, 30d, 2w, 1m")
                ctx.exit(1)
        registry = ImageRegistry(reg_path)
        registry.load()
        cutoff = (datetime.now(timezone.utc) - delta) if delta else None
        to_remove: list[str] = []
        for e in registry.entries:
            if older_than and cutoff and e.last_used:
                try:
                    lu = datetime.fromisoformat(e.last_used.replace("Z", "+00:00"))
                    if lu.tzinfo is None:
                        lu = lu.replace(tzinfo=timezone.utc)
                    if lu < cutoff:
                        to_remove.append(e.name)
                except ValueError:
                    pass
            if unused and (e.usage_count or 0) == 0:
                to_remove.append(e.name)
        to_remove = list(dict.fromkeys(to_remove))
        if not to_remove:
            print_info("No images match --older-than/--unused.")
            return
        docker = DockerManager()
        for name in to_remove:
            entry = registry.find_by_name(name)
            if not entry:
                continue
            tag = entry.docker_tag
            if dry_run:
                console.print(f"  Would remove: {name} (Docker: {tag})")
                continue
            if docker.image_exists(tag):
                docker.remove_image(tag, force=True)
            tar_path = project_dir / entry.file if not Path(entry.file).is_absolute() else Path(entry.file)
            if tar_path.exists():
                tar_path.unlink()
            registry.remove(name)
        if not dry_run:
            registry.save()
            print_success(f"Removed {len(to_remove)} image(s) from registry and disk.")
        else:
            print_info(f"Dry-run: would remove {len(to_remove)} image(s).")
        return

    if not clean_all:
        print_info("Nothing to clean. Use --all, --older-than, or --unused.")
        return

    # --all: remove all capy Docker images (and optionally registry entries)
    result = _run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
    if result.returncode != 0:
        print_error(result.stderr.strip() or "Failed to list Docker images.")
        ctx.exit(result.returncode)

    targets = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("capy-ubuntu-")
    ]
    if not targets:
        print_info("No localci capy images found.")
        return

    if dry_run:
        print_info("Dry-run: would remove the following images:")
        for t in targets:
            console.print(f"  - {t}")
        return

    rm = _run(["docker", "rmi", "-f", *targets])
    if rm.returncode != 0:
        print_error(rm.stderr.strip() or "Failed to remove one or more images.")
        ctx.exit(rm.returncode)
    print_success(f"Removed {len(targets)} image(s).")


# ---------------------------------------------------------------------------
# localci images import
# ---------------------------------------------------------------------------


@images.command("import")
@click.argument("tar_file", type=click.Path(exists=True))
@click.pass_context
def images_import(ctx: click.Context, tar_file: str) -> None:
    """Import a Docker image from a tar file."""
    result = _run(["docker", "load", "-i", tar_file])
    if result.returncode != 0:
        print_error(result.stderr.strip() or "Failed to import image.")
        ctx.exit(result.returncode)
    print_success("Imported image.")
    if result.stdout.strip():
        print_info(result.stdout.strip())


# ---------------------------------------------------------------------------
# localci images export
# ---------------------------------------------------------------------------


@images.command("export")
@click.argument("image")
@click.option("--output", "-o", "output_path", type=click.Path(), required=True, help="Output tar file path.")
@click.pass_context
def images_export(ctx: click.Context, image: str, output_path: str) -> None:
    """Export a Docker image to a tar file."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = _run(["docker", "save", "-o", str(out), image])
    if result.returncode != 0:
        print_error(result.stderr.strip() or "Failed to export image.")
        ctx.exit(result.returncode)
    print_success(f"Exported image {image} to {out}")
