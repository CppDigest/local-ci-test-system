"""Built-in and entry-point patch step registration.

Built-in steps live in :data:`localci.core.patch_steps.PATCH_STEP_REGISTRY`.
Additional steps register via the ``localci.patch_steps`` entry-point group::

    [project.entry-points."localci.patch_steps"]
    my_step = "my_package.patch_steps:MyCustomStep"
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from localci.core.config import PATCH_STEP_NAMES

if TYPE_CHECKING:
    from localci.core.patch_pipeline import PatchStep


@lru_cache(maxsize=1)
def get_patch_step_registry() -> dict[str, type[PatchStep]]:
    """Return built-in patch steps merged with entry-point plugins."""
    from localci.core.patch_steps import PATCH_STEP_REGISTRY

    registry: dict[str, type[PatchStep]] = dict(PATCH_STEP_REGISTRY)
    for ep in entry_points(group="localci.patch_steps"):
        if ep.name in registry:
            continue
        registry[ep.name] = ep.load()
    return registry


def clear_patch_step_registry_cache() -> None:
    """Clear the registry cache (for tests that register plugins at runtime)."""
    get_patch_step_registry.cache_clear()


def is_builtin_patch_step(name: str) -> bool:
    """Return True when *name* is a built-in (non-plugin) patch step."""
    return name in PATCH_STEP_NAMES
