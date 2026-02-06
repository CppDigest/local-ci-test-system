"""``localci images`` command group.

Manage Docker images: list, inspect, build, clean, import, and export.
"""

from __future__ import annotations

import click

from localci.utils.output import (
    console,
    print_info,
    print_not_implemented,
    print_success,
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
    # TODO: Replace with ImageRegistry from Issue 3.
    print_not_implemented("images list (image registry backend)")


# ---------------------------------------------------------------------------
# localci images info
# ---------------------------------------------------------------------------


@images.command("info")
@click.argument("image")
@click.pass_context
def images_info(ctx: click.Context, image: str) -> None:
    """Show detailed information about an image."""
    # TODO: Replace with ImageRegistry from Issue 3.
    print_not_implemented("images info (image registry backend)")
    print_info(f"Would show details for image: {image}")


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
    # TODO: Replace with Docker Image Management from Issue 4.
    print_not_implemented("images build (docker image management backend)")
    if build_all:
        print_info("Would build all missing images")
    elif image_names:
        print_info(f"Would build images: {', '.join(image_names)}")
    else:
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
    # TODO: Replace with Docker Image Management from Issue 4.
    print_not_implemented("images clean (docker image management backend)")


# ---------------------------------------------------------------------------
# localci images import
# ---------------------------------------------------------------------------


@images.command("import")
@click.argument("tar_file", type=click.Path(exists=True))
@click.pass_context
def images_import(ctx: click.Context, tar_file: str) -> None:
    """Import a Docker image from a tar file."""
    # TODO: Replace with Docker Image Management from Issue 4.
    print_not_implemented("images import (docker image management backend)")
    print_info(f"Would import image from: {tar_file}")


# ---------------------------------------------------------------------------
# localci images export
# ---------------------------------------------------------------------------


@images.command("export")
@click.argument("image")
@click.option("--output", "-o", "output_path", type=click.Path(), required=True, help="Output tar file path.")
@click.pass_context
def images_export(ctx: click.Context, image: str, output_path: str) -> None:
    """Export a Docker image to a tar file."""
    # TODO: Replace with Docker Image Management from Issue 4.
    print_not_implemented("images export (docker image management backend)")
    print_info(f"Would export image {image} to {output_path}")
