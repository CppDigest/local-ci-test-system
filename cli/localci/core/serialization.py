"""Serialization helpers for :class:`~localci.core.workflow.Workflow` objects.

Provides JSON and dict conversion so that ``localci analyze --format json``
and downstream consumers can work with plain data structures.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from localci.core.workflow import (
    Platform,
    Workflow,
)


# =====================================================================
# JSON encoder
# =====================================================================


class WorkflowEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles enums, dataclasses, and paths."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, Path):
            return str(obj)
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        # Fallback: convert to string rather than raising TypeError
        return str(obj)


# =====================================================================
# Public helpers
# =====================================================================


def workflow_to_json(workflow: Workflow, indent: int = 2) -> str:
    """Serialize *workflow* to a JSON string."""
    return json.dumps(workflow, cls=WorkflowEncoder, indent=indent)


def workflow_to_dict(workflow: Workflow) -> dict:
    """Convert *workflow* to a plain ``dict`` (JSON round-trip)."""
    return json.loads(workflow_to_json(workflow))


def workflow_summary(workflow: Workflow) -> dict:
    """Generate a concise summary dict for *workflow*."""
    return {
        "name": workflow.name,
        "file": str(workflow.file_path),
        "events": workflow.events,
        "total_jobs": workflow.total_jobs,
        "total_matrix_entries": workflow.total_matrix_entries,
        "platform_summary": {
            p.value: count for p, count in workflow.platform_summary().items()
        },
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "matrix_count": job.total_configurations,
                "needs": job.needs,
            }
            for job in workflow.jobs.values()
        ],
        "dependency_order": workflow.dependency_order(),
    }
