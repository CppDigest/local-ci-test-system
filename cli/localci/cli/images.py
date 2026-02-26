"""``localci images`` command group.

Manage Docker images: list, inspect, build, clean, import, and export.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import click
import yaml

from localci.utils.output import (
    console,
    make_table,
    print_error,
    print_info,
    print_success,
    print_warning,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGES_DIR = REPO_ROOT / "images" / "capy"
REGISTRY_FILE = REPO_ROOT / "image-registry.yml"


def _load_registry() -> list[dict[str, Any]]:
    if not REGISTRY_FILE.exists():
        raise FileNotFoundError(f"Registry file not found: {REGISTRY_FILE}")
    data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8")) or {}
    return data.get("images", [])


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
@click.pass_context
def images_list(ctx: click.Context, output_format: str) -> None:
    """List available images."""
    try:
        registry_images = _load_registry()
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc))
        ctx.exit(1)
        return

    if output_format == "json":
        click.echo(json.dumps(registry_images, indent=2))
        return

    table = make_table("Name", "Tag", "OS", "Arch", "Variants", title="Image Registry")
    for img in registry_images:
        variants = ", ".join(img.get("variants", [])) or "-"
        table.add_row(
            img.get("name", "-"),
            img.get("docker_tag", "-"),
            img.get("os", "-"),
            str(img.get("architecture", "-")),
            variants,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# localci images info
# ---------------------------------------------------------------------------


@images.command("info")
@click.argument("image")
@click.pass_context
def images_info(ctx: click.Context, image: str) -> None:
    """Show detailed information about an image."""
    try:
        registry_images = _load_registry()
    except Exception as exc:  # noqa: BLE001
        print_error(str(exc))
        ctx.exit(1)
        return

    match = next((i for i in registry_images if i.get("name") == image), None)
    if not match:
        print_warning(f"Image not found in registry: {image}")
        ctx.exit(1)
        return

    click.echo(yaml.safe_dump(match, sort_keys=False))


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

    if not IMAGES_DIR.exists():
        print_error(f"Images directory not found: {IMAGES_DIR}")
        ctx.exit(1)
        return

    build_all_script = IMAGES_DIR / "build-all.sh"
    build_one_script = IMAGES_DIR / "build-one.sh"

    if build_all:
        cmd = ["bash", str(build_all_script), "--save"]
        result = _run(cmd)
        if result.returncode != 0:
            print_error(result.stderr.strip() or "Build failed.")
            ctx.exit(result.returncode)
            return
        print_success("Built all images.")
        return

    if image_names:
        for image in image_names:
            cmd = ["bash", str(build_one_script), image, "--save"]
            result = _run(cmd)
            if result.returncode != 0:
                print_error(result.stderr.strip() or f"Build failed for {image}.")
                ctx.exit(result.returncode)
                return
            print_success(f"Built image: {image}")
        return

    print_info("No images specified. Use --all or provide image names.")


# ---------------------------------------------------------------------------
# localci images clean
# ---------------------------------------------------------------------------


@images.command("clean")
@click.option("--older-than", type=str, default=None, help="Remove images older than (e.g. 30d).")
@click.option("--unused", is_flag=True, help="Remove unused images.")
@click.option("--all", "clean_all", is_flag=True, help="Remove all localci images.")
@click.option("--dry-run", is_flag=True, help="Preview without removing.")
@click.pass_context
def images_clean(
    ctx: click.Context,
    older_than: str | None,
    unused: bool,
    clean_all: bool,
    dry_run: bool,
) -> None:
    """Clean up Docker images."""
    if older_than:
        print_warning("--older-than is not yet implemented; ignoring.")
    if unused:
        print_warning("--unused is not yet implemented; ignoring.")

    if not clean_all:
        print_info("Nothing to clean. Use --all to remove localci images.")
        return

    result = _run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
    if result.returncode != 0:
        print_error(result.stderr.strip() or "Failed to list Docker images.")
        ctx.exit(result.returncode)
        return

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
        return
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
        return
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
        return
    print_success(f"Exported image {image} to {out}")
