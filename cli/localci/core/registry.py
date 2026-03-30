"""Image registry and two-mark matching algorithm.

Implements the image registry schema (image-registry.yml), essential/extra
marks calculation, and image selection per Design Guide Appendix D.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import yaml

if TYPE_CHECKING:
    from localci.core.workflow import MatrixEntry

logger = logging.getLogger(__name__)

REGISTRY_VERSION = "1.0"

# Essential marks: OS+version+arch = 70, compiler match = +30 → max 100
MARKS_OS_ARCH = 70
MARKS_COMPILER = 30
# Extra marks: per package +10, per build tool +20
MARKS_PER_PACKAGE = 10
MARKS_PER_TOOL = 20


@dataclass
class RegistryEntry:
    """Single image entry in the registry (image-registry.yml schema)."""

    name: str
    file: str
    docker_tag: str
    os: str  # e.g. "ubuntu:25.04"
    architecture: str  # x86_64 or x86
    packages: list[str] = field(default_factory=list)
    compilers: list[str] = field(default_factory=list)  # e.g. ["gcc-15", "clang-17"]
    tools: list[str] = field(default_factory=list)  # build tools e.g. ["cmake", "lcov"]
    size_mb: Optional[int] = None
    created: Optional[str] = None
    last_used: Optional[str] = None
    usage_count: int = 0
    # Optional fields from existing registry (preserved on load/save)
    variants: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegistryEntry:
        """Build RegistryEntry from YAML/dict (e.g. image-registry.yml)."""
        packages = d.get("packages") or []
        tools = d.get("tools") or []
        return cls(
            name=str(d.get("name", "")),
            file=str(d.get("file", "")),
            docker_tag=str(d.get("docker_tag", "")),
            os=str(d.get("os", "")),
            architecture=str(d.get("architecture", "x86_64")),
            packages=packages if isinstance(packages, list) else [],
            compilers=d.get("compilers") or [],
            tools=tools if isinstance(tools, list) else [],
            size_mb=d.get("size_mb"),
            created=d.get("created"),
            last_used=d.get("last_used"),
            usage_count=int(d.get("usage_count", 0)),
            variants=d.get("variants") or [],
            raw={k: v for k, v in d.items() if k not in {"name", "file", "docker_tag", "os", "architecture", "packages", "compilers", "tools", "size_mb", "created", "last_used", "usage_count", "variants"}},
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "file": self.file,
            "docker_tag": self.docker_tag,
            "os": self.os,
            "architecture": self.architecture,
            "compilers": self.compilers,
            "packages": self.packages or None,
            "tools": self.tools or None,
            "size_mb": self.size_mb,
            "created": self.created,
            "last_used": self.last_used,
            "usage_count": self.usage_count,
            "variants": self.variants or None,
        }
        out = {k: v for k, v in out.items() if v is not None}
        out.update(self.raw)
        return out


@dataclass
class MatchResult:
    """Result of image selection for a matrix entry."""

    use_image: Optional[RegistryEntry] = None  # Use this image (essential=100)
    needs_build: bool = False
    base_image: Optional[RegistryEntry] = None  # When needs_build, use this as base
    essential_marks: int = 0
    extra_marks: int = 0


def _entry_os_arch(entry: "MatrixEntry") -> tuple[str, str]:
    """(os_string, architecture) for matrix entry. e.g. ('ubuntu:25.04', 'x86_64')."""
    if entry.container.image:
        os_str = entry.container.image.strip().lower()
    else:
        os_str = (entry.runs_on or "ubuntu-latest").strip().lower()
        if os_str == "ubuntu-latest":
            os_str = "ubuntu:24.04"
        elif "-" in os_str:
            part = os_str.split("-")[0]
            ver = os_str.split("-")[-1] if "-" in os_str else "latest"
            if part == "ubuntu" and ver != "latest":
                os_str = f"ubuntu:{ver}"
    arch = getattr(entry, "architecture", "x86_64") or "x86_64"
    return os_str, arch


def _entry_compiler_key(entry: "MatrixEntry") -> str:
    """Compiler key for matrix entry, e.g. 'gcc-15', 'clang-17'."""
    family = entry.compiler.family.value
    version = entry.compiler.version or "*"
    return f"{family}-{version}"


def essential_marks(entry: "MatrixEntry", reg: RegistryEntry) -> int:
    """Compute essential marks (0, 70, or 100) for registry image vs matrix entry.

    - 0: OS+version+architecture mismatch (cannot use).
    - 70: OS+version+arch match, compiler differs (can use as base).
    - 100: OS+version+arch and compiler match (full match).
    """
    req_os, req_arch = _entry_os_arch(entry)
    img_os = (reg.os or "").strip().lower()
    img_arch = (reg.architecture or "x86_64").strip().lower()
    req_arch = (req_arch or "x86_64").strip().lower()

    if img_os != req_os or img_arch != req_arch:
        return 0

    marks = MARKS_OS_ARCH
    req_compiler = _entry_compiler_key(entry)
    if not req_compiler or req_compiler.endswith("-*"):
        return marks
    reg_compilers = [c.strip().lower() for c in (reg.compilers or [])]
    if any(c == req_compiler.lower() for c in reg_compilers):
        marks += MARKS_COMPILER
    return marks


def extra_marks(entry: "MatrixEntry", reg: RegistryEntry) -> int:
    """Compute extra marks (packages +10 each, build tools +20 each). Only meaningful when essential_marks > 0."""
    total = 0
    req_apt = set(
        p.strip().lower()
        for p in (getattr(entry.packages, "apt_packages", None) or [])
        if p
    )
    req_tools = set(
        t.strip().lower()
        for t in (getattr(entry.packages, "build_tools", None) or [])
        if t
    )
    reg_packages = set(p.strip().lower() for p in (reg.packages or []) if p)
    reg_tools = set(t.strip().lower() for t in (reg.tools or []) if t)
    for p in req_apt:
        if p in reg_packages:
            total += MARKS_PER_PACKAGE
    for t in req_tools:
        if t in reg_tools:
            total += MARKS_PER_TOOL
        elif t in reg_packages:
            total += MARKS_PER_PACKAGE
    return total


def _tie_break(best: RegistryEntry, other: RegistryEntry) -> RegistryEntry:
    """Prefer most recently used, then highest usage_count, then smallest size_mb."""
    def key(r: RegistryEntry) -> tuple[int, int, int]:
        # last_used: prefer later (higher timestamp value for sorting desc)
        try:
            lu = r.last_used or ""
            ts = int(datetime.fromisoformat(lu.replace("Z", "+00:00")).timestamp()) if lu else 0
        except Exception:
            ts = 0
        # usage_count: higher better
        uc = r.usage_count or 0
        # size_mb: smaller better (negate so higher is better for max)
        sz = -(r.size_mb or 0)
        return (ts, uc, sz)
    return best if key(best) >= key(other) else other


def select_image(entry: "MatrixEntry", registry_entries: list[RegistryEntry]) -> MatchResult:
    """Select best image for this matrix entry using two-mark algorithm.

    - If any image has essential_marks == 100: choose one with highest extra_marks (tie-break).
    - Else: choose image with highest essential_marks as base (needs_build=True); tie-break by extra then tie_break.
    """
    if not registry_entries:
        return MatchResult(needs_build=True)

    scored: list[tuple[int, int, RegistryEntry]] = []
    for reg in registry_entries:
        ess = essential_marks(entry, reg)
        if ess == 0:
            continue
        ext = extra_marks(entry, reg)
        scored.append((ess, ext, reg))

    if not scored:
        return MatchResult(needs_build=True)

    full_matches = [(ext, r) for ess, ext, r in scored if ess == 100]
    if full_matches:
        best_ext = max(e for e, _ in full_matches)
        candidates = [r for e, r in full_matches if e == best_ext]
        best = candidates[0]
        for other in candidates[1:]:
            best = _tie_break(best, other)
        return MatchResult(
            use_image=best,
            needs_build=False,
            essential_marks=100,
            extra_marks=extra_marks(entry, best),
        )

    best_ess = max(s[0] for s in scored)
    candidates = [(ext, r) for ess, ext, r in scored if ess == best_ess]
    best_ext = max(e for e, _ in candidates)
    base_candidates = [r for e, r in candidates if e == best_ext]
    best = base_candidates[0]
    for other in base_candidates[1:]:
        best = _tie_break(best, other)
    return MatchResult(
        needs_build=True,
        base_image=best,
        essential_marks=best_ess,
        extra_marks=extra_marks(entry, best),
    )


class ImageRegistry:
    """In-memory image registry with load/save and CRUD."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self.entries: list[RegistryEntry] = []
        self._version = REGISTRY_VERSION

    def load(self, path: Optional[Path] = None) -> None:
        p = path or self.path
        if not p or not p.exists():
            self.entries = []
            self._version = REGISTRY_VERSION
            return
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        self._version = str(data.get("version", REGISTRY_VERSION))
        images = data.get("images") or []
        self.entries = [RegistryEntry.from_dict(i) for i in images if isinstance(i, dict)]

    def save(self, path: Optional[Path] = None) -> None:
        p = path or self.path
        if not p:
            raise ValueError("No path set for registry save")
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self._version,
            "images": [e.to_dict() for e in self.entries],
        }
        p.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False), encoding="utf-8")

    def find_by_name(self, name: str) -> Optional[RegistryEntry]:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    def add(self, entry: RegistryEntry) -> None:
        if self.find_by_name(entry.name):
            raise ValueError(f"Registry already has an image named {entry.name!r}")
        self.entries.append(entry)

    ALLOWED_UPDATE_FIELDS: frozenset[str] = frozenset({
        "docker_tag", "file", "size_mb", "packages",
        "last_used", "usage_count", "build_date",
    })

    def update(self, name: str, **kwargs: Any) -> None:
        e = self.find_by_name(name)
        if not e:
            raise ValueError(f"No image named {name!r} in registry")
        for k, v in kwargs.items():
            if k not in self.ALLOWED_UPDATE_FIELDS:
                raise ValueError(
                    f"Field {k!r} is not allowed in registry update; "
                    f"permitted fields: {sorted(self.ALLOWED_UPDATE_FIELDS)}"
                )
            setattr(e, k, v)

    def update_usage(self, name: str) -> None:
        """Increment usage_count and set last_used to now (ISO)."""
        e = self.find_by_name(name)
        if not e:
            return
        e.usage_count = (e.usage_count or 0) + 1
        e.last_used = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def remove(self, name: str) -> bool:
        for i, e in enumerate(self.entries):
            if e.name == name:
                self.entries.pop(i)
                return True
        return False

    def select(self, entry: "MatrixEntry") -> MatchResult:
        """Select best image for this matrix entry."""
        return select_image(entry, self.entries)
