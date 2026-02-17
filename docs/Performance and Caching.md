WORK ITEM
Owner: Brad
Date: 2026-02-12
Status: in-progress
Time Spent (today): 1h
Time Spent (total): 1h
Repo/Area: CppDigest/local-ci-test-system
GitHub Issues: TBD (Issues 9, 10, 11)
GitHub PRs:
Related Links: 2026-02/2026-02-06/brad/Local CI sytem for capy preparation.md
Invoice Notes:
Tags: local-ci, capy, caching, ccache, boost, cmake, phase-2, performance
---
Title: Local CI Phase 2 — Performance (Caching and Optimization)

## Overview

Phase 2 (Sprint 2 in the implementation priority order) focuses on **performance improvement** through caching. It addresses three of the five bottlenecks identified in the preparation document: repeated B2 builds, repeated Boost clone, and repeated CMake configure. Delivering Issues 9, 10, and 11 will reduce full-run and incremental-run times so that the system can approach the target of **&lt; 2 minutes for full Linux CI** and **&lt; 30 seconds for incremental build**.


## What I did
- analyze design guide and work plan for the next step
- created issues and sub-issues for phase 2, 3
- requested Ubuntu VPS with enough resources for parallel job
- setup ssh key login


**Prerequisite:** Phase 1 components (CLI, Workflow Analyzer, Job Executor, Linux Base Images) must be functional and validated (e.g. Linux jobs running successfully on Ubuntu).

**Phase 2 components:**

1. **Issue 9: Build Artifact Caching (ccache/sccache)** — Cache compilation artifacts across runs.
2. **Issue 10: Boost Dependency Caching** — Pre-clone and cache the Boost superproject; incremental updates only.
3. **Issue 11: CMake Configuration Caching** — Persist CMake cache and skip configure when inputs are unchanged.

**Priority:** Critical (per preparation document).  
**Target:** Cache hit rate &gt; 90% after warm-up; incremental build &lt; 30 seconds.

---

## Architecture Context

```
┌─────────────────────────────────────────────────────────────────┐
│                      MCP / CLI                                    │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    Job Executor (act)        Phase 1               │
│                    Linux Base Images                             │
└─────────────────────┬───────────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ Build         │ │ Boost         │ │ CMake         │  Phase 2
│ Artifact      │ │ Dependency    │ │ Config        │  (Caching)
│ Cache         │ │ Cache         │ │ Cache         │
│ (Issue 9)     │ │ (Issue 10)    │ │ (Issue 11)    │
│ ccache/sccache│ │ clone/update  │ │ CMakeCache    │
└───────────────┘ └───────────────┘ └───────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                 ┌─────────────────┐
                 │ Host or volume  │
                 │ cache dirs      │
                 │ (~/.localci/    │
                 │  cache/...)    │
                 └─────────────────┘
```

Phase 2 adds cache layers that are mounted or bind-mounted into containers (or used by the executor) so that repeated runs reuse build artifacts, Boost tree, and CMake configuration instead of recomputing them.

---

## Phase 2 Components

### Issue 9: Build Artifact Caching (ccache/sccache)

**Scope:** Cache compilation artifacts across runs so that unchanged translation units are not recompiled.

**Deliverables:**

- ccache (or sccache) integration for the capy/B2 and CMake build steps.
- Cache directory management: configurable path (e.g. `~/.localci/cache/ccache`), size limits, cleanup policy.
- Cache hit/miss reporting: expose stats (hit rate, size) in CLI output or logs.
- Cache invalidation strategy: document when cache is invalidated (clean commands, compiler/toolchain change, manual clear).

**Dependencies:** Issue 5 (Job Executor). Containers must have ccache/sccache installed and configured (Phase 1 images may already include ccache).

**Integration:** Executor or run command passes cache directory into the container (bind mount); environment variables (e.g. `CCACHE_DIR`, `CCACHE_MAXSIZE`) set for the job.

**Design reference:** Preparation doc — Bottleneck “B2 Build: Full build from scratch each time”.

---

### Issue 10: Boost Dependency Caching

**Scope:** Pre-clone and cache the Boost superproject so jobs do not clone Boost on every run.

**Deliverables:**

- One-time or on-demand Boost clone/update into a shared cache directory (e.g. `~/.localci/cache/boost` or per-branch).
- Shallow clone support where appropriate to reduce size and time.
- Branch-specific caching (e.g. `develop`, `master`) so different workflows use the correct tree.
- Incremental updates only: `git fetch` / `git pull` when cache exists; full clone only when missing.
- Optional: integrate with pre-built images (Phase 1 Issue 12) so images can ship with Boost already cloned; local run then uses that or overlays cache.

**Dependencies:** Issue 9 is listed in the preparation doc as dependency; in practice Issue 10 can proceed in parallel with Issue 9, both depending on Issue 5.

**Integration:** Cache path mounted into the container; workflow steps use the cached Boost path instead of cloning. May require workflow or action changes (e.g. “use existing Boost” step) or executor-level injection.

**Design reference:** Preparation doc — Bottleneck “Boost Clone: Clones entire Boost superproject every run”.

---

### Issue 11: CMake Configuration Caching

**Scope:** Skip CMake configure when CMakeLists.txt, toolchain, and other inputs are unchanged.

**Deliverables:**

- CMake cache persistence: store `CMakeCache.txt` and CMake generated files in a cache directory (e.g. per job or per matrix entry).
- Change detection: compare inputs (CMakeLists.txt, toolchain file, key env vars) and only reconfigure when changed.
- Incremental reconfiguration: when inputs change, run CMake again; when unchanged, reuse existing configuration.

**Dependencies:** Issue 10 (per preparation doc). Logically depends on a stable workspace/cache layout so that CMake cache paths remain valid across runs.

**Integration:** Cache directory for CMake build tree (or at least `CMakeCache.txt` and generated files) mounted or restored per job; executor or run step sets `CMAKE_BUILD_DIR` or similar so the job uses the cached config.

**Design reference:** Preparation doc — Bottleneck “CMake Configure: Reconfigures even when unchanged”.

---

## Data Flow and Cache Layout

- **Host cache root:** e.g. `~/.localci/cache/` (or value from `.localci.yml`).
- **Subdirectories (suggested):**
  - `ccache/` — build artifact cache (Issue 9).
  - `boost/` or `boost/<branch>/` — Boost superproject clone (Issue 10).
  - `cmake/<job_id>/<matrix_key>/` — CMake cache and generated files per job/matrix (Issue 11).
- **Visibility:** Cache dirs must be bind-mounted into the container at known paths so that B2, CMake, and Boost steps use them. Environment variables (e.g. `CCACHE_DIR`, `BOOST_ROOT`, `CMAKE_BINARY_DIR`) must be set accordingly.

---

## Configuration (.localci.yml)

Phase 2 extends the existing `cache` section used in Phase 1 design:

```yaml
# .localci.yml — Cache settings (Phase 2)

cache:
  enabled: true
  directory: ~/.localci/cache   # Host cache root

  # Build artifact cache (Issue 9)
  ccache:
    enabled: true
    max_size: "2G"
    dir: ~/.localci/cache/ccache

  # Boost dependency cache (Issue 10)
  boost:
    enabled: true
    dir: ~/.localci/cache/boost
    branch: develop             # or master
    shallow: true

  # CMake config cache (Issue 11)
  cmake:
    enabled: true
    dir: ~/.localci/cache/cmake
```

CLI flags (e.g. `--no-cache`, `--cache-dir`) can override or disable caches for debugging.

---

## Success Criteria (from Preparation Document)

| Metric | Target |
|--------|--------|
| Full Linux CI | &lt; 2 minutes (from 12–15 min) |
| Incremental build | &lt; 30 seconds |
| Single job execution | &lt; 15 seconds (with warm cache) |
| Cache hit rate | &gt; 90% after warm-up |

---

## Phase 2 Deliverables Summary

| Component | Status | Scope |
|-----------|--------|-------|
| Build Artifact Cache (Issue 9) | Done | ccache bind mount, CCACHE_DIR/CCACHE_MAXSIZE, config dir/max_size |
| Boost Dependency Cache (Issue 10) | Done | Pre-clone/fetch in `boost_cache.py`, branch/shallow, BOOST_ROOT mount |
| CMake Config Cache (Issue 11) | Done | Per-job dir mount, LOCALCI_CMAKE_CACHE_DIR |
| Cache config in .localci.yml | Done | cache.ccache, cache.boost, cache.cmake; --no-cache, --cache-dir |
| Documentation | Done | USER_GUIDE.md cache section, invalidation notes |

---

## Implementation Order

1. **Issue 9: Build Artifact Caching** — Unblocks immediate win for B2/compilation; images may already have ccache installed.
2. **Issue 10: Boost Dependency Caching** — Largest single time saver; clone once, reuse.
3. **Issue 11: CMake Configuration Caching** — Builds on stable layout; smaller but meaningful for projects using CMake.

Issues 9 and 10 can be parallelized; Issue 11 can follow or overlap with Issue 10.

---

## Next Steps

1. ~~Create GitHub issues 9, 10, 11~~ (optional; implementation complete).
2. Measure full Linux CI and incremental build times before/after; tune cache sizes and invalidation.
3. Optional: ccache hit/miss reporting (stats in CLI or logs).
4. Optional: workflow steps that honour `BOOST_ROOT` / `LOCALCI_CMAKE_CACHE_DIR` to skip clone or reconfigure.

---

## Reference

- **Preparation document:** `2026-02/2026-02-06/brad/Local CI sytem for capy preparation.md` — Child Issues Breakdown (Phase 3: Caching and Optimization = Issues 9, 10, 11), Implementation Priority Order (Sprint 2: Performance).
- **Phase 1 summary:** `2026-02/2026-02-06/brad/Local CI Phase 1 - Core Infrastructure.md` — Foundation that Phase 2 builds on.
