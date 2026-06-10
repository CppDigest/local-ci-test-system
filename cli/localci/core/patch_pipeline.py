"""Configurable workflow patch pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from localci.core.config import PATCH_STEP_NAMES, LocalCIConfig

if TYPE_CHECKING:
    from localci.core.workflow import MatrixEntry


class PatchStep(ABC):
    """A single workflow transformation applied to workflow text lines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique step identifier (matches ``PatchesConfig`` field names)."""

    @abstractmethod
    def apply(self, ctx: "PatchContext") -> None:
        """Apply this patch in place to ``ctx.lines``."""


@dataclass
class PatchContext:
    """Mutable state passed through the patch pipeline.

    Patches operate on workflow text lines (not parsed YAML) to avoid
    round-trip formatting changes that would alter act behaviour.
    """

    lines: list[str]
    entry: "MatrixEntry"
    config: LocalCIConfig
    image_tag: Optional[str] = None
    job_id: Optional[str] = None
    container_mount_options: Optional[str] = None


class PatchPipeline:
    """Apply an ordered sequence of patch steps to workflow content."""

    def __init__(self, steps: list[PatchStep]) -> None:
        self._steps = steps

    @property
    def steps(self) -> list[PatchStep]:
        """Configured patch steps in pipeline order."""
        return list(self._steps)

    @classmethod
    def from_config(cls, config: LocalCIConfig) -> PatchPipeline:
        """Build a pipeline from ``.localci.yml`` patch settings."""
        from localci.core.patch_steps import PATCH_STEP_REGISTRY

        patches = config.patches
        order = patches.order or list(PATCH_STEP_NAMES)
        steps: list[PatchStep] = []
        for name in order:
            if not getattr(patches, name, True):
                continue
            step_cls = PATCH_STEP_REGISTRY.get(name)
            if step_cls is None:
                raise ValueError(
                    f"Patch step {name!r} is enabled but not registered; "
                    "check PATCH_STEP_NAMES / PATCH_STEP_REGISTRY alignment"
                )
            steps.append(step_cls())
        return cls(steps)

    def apply(self, ctx: PatchContext) -> None:
        """Run all configured steps sequentially."""
        for step in self._steps:
            step.apply(ctx)
