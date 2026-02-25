Title: Local CI Phase 2 — Performance (Caching and Optimization)

## Overview

Phase 2 (Sprint 2 in the implementation priority order) focuses on **performance improvement** through caching. It addresses three of the five bottlenecks identified in the preparation document: repeated B2 builds, repeated Boost clone, and repeated CMake configure. Delivering Issues 9, 10, and 11 will reduce full-run and incremental-run times so that the system can approach the target of **&lt; 2 minutes for full Linux CI** and **&lt; 30 seconds for incremental build**.


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

**Alignment with Boost-hands-on-exp:** Local CI uses the same conceptual approach for incremental build and caching as the [Boost-hands-on-exp](https://github.com/boostorg/Boost-hands-on-exp) project: (1) **Incremental b2** — a persistent directory (b2-source per job) holds boost-root plus b2’s bin.v2; on cache hit the workflow uses that tree so b2 only rebuilds what changed. (2) **Compiler cache** — ccache wraps the compiler (CC/CXX set to `ccache gcc` / `ccache g++`) so every compilation is cached. Cold vs warm runs behave the same way as in Boost-hands-on-exp’s Step 3/4 scripts.

---

## Phase 2 Components

### Issue 9: Build Artifact Caching (ccache/sccache)

**Scope:** Cache compilation artifacts across runs so that unchanged translation units are not recompiled.

**Deliverables:**

- ccache (or sccache) integration for the capy/B2 and CMake build steps. ✅
- Cache directory management: configurable path (e.g. `~/.localci/cache/ccache`), size limits (`max_size`), optional `compress`. ✅
- Cache hit/miss reporting: after each run, `localci run` prints `ccache -s` output when host has ccache; `localci cache stats` shows stats on demand. ✅
- Cache invalidation: `localci run --no-cache` disables caches; `localci cache clear [--target ccache]` removes cache dirs; documented in USER_GUIDE. ✅

**Dependencies:** Issue 5 (Job Executor). Containers must have ccache/sccache installed and configured (Phase 1 images may already include ccache).

**Integration:** Executor bind-mounts host ccache dir; sets `CCACHE_DIR`, `CCACHE_MAXSIZE`, `CCACHE_COMPRESS` for the job. When ccache is enabled, CC and CXX are set to `ccache gcc` and `ccache g++` (or `ccache <matrix-compiler>`) so b2 and other build steps use ccache — same approach as Boost-hands-on-exp.

**Design reference:** Preparation doc — Bottleneck “B2 Build: Full build from scratch each time”.

---

### Issue 10: Boost Dependency Caching

**Scope:** Pre-clone and cache the Boost superproject so jobs do not clone Boost on every run.

**Deliverables:**

- One-time or on-demand Boost clone/update into a shared cache directory (e.g. `~/.localci/cache/boost`). ✅
- Shallow clone support (`cache.boost.shallow`). ✅
- Branch-specific: `cache.boost.branch` (e.g. `develop`, `master`); single cache dir updated to that branch. ✅
- Configurable remote: `cache.boost.remote` (default https://github.com/boostorg/boost.git). ✅
- Incremental updates: when cache exists, `git fetch` + `git reset --hard origin/<branch>`; full clone only when missing. ✅
- Submodules: after clone or fetch+reset, `git submodule update --init --recursive` so `tools/build` and libs are present (required for `bootstrap.sh` and B2). ✅
- `localci cache update` refreshes Boost cache without running CI. ✅
- Optional: integrate with pre-built images (Issue 12); not yet implemented.

**Dependencies:** Issue 9 is listed in the preparation doc as dependency; in practice Issue 10 can proceed in parallel with Issue 9, both depending on Issue 5.

**Integration:** Cache path bind-mounted; `BOOST_ROOT` set in job env. **Local CI patches the workflow** so the Clone Boost step runs only when `BOOST_ROOT` is empty; when `BOOST_ROOT` is set (cached Boost), the step is skipped and a "Use cached Boost (BOOST_ROOT)" step creates `boost-source` from the cache so the Patch step works. Workflow authors can also implement this manually (documented in USER_GUIDE).

**B2 source + build artifacts cache (`b2-source`):** When `cache.boost.build_dir` is true (default), Local CI caches the **entire per-job `boost-root`** directory (e.g. `~/.localci/cache/b2-source/<job_matrix_key>`) and bind-mounts it at `/tmp/localci-cache/b2-source`, setting `LOCALCI_B2_SOURCE_DIR`. The workflow patcher replaces the `cp -rL boost-source boost-root` in the Patch Boost step with an incremental approach: when the cache exists, `rsync` updates only changed Boost source files from `$BOOST_ROOT`, preserving `bin.v2/` (b2 artifacts) and `libs/capy`, then symlinks `boost-root` to the cache dir; on the first run it falls back to `cp -rL` and seeds the cache for next time. This means b2 sees stable timestamps on unchanged files and its `bin.v2/` object files persist across runs, so only the modified files and their dependees are rebuilt (&lt;10s target). Clear with `localci cache clear --target b2-source`.

**Design reference:** Preparation doc — Bottleneck “Boost Clone: Clones entire Boost superproject every run”.

---

### Issue 11: CMake Configuration Caching

**Scope:** Skip CMake configure when CMakeLists.txt, toolchain, and other inputs are unchanged.

**Deliverables:**

- CMake cache persistence: per-job/matrix directory bind-mounted; path keyed by job/matrix and **input digest** so unchanged inputs reuse the same dir. ✅
- Change detection: digest of `CMakeLists.txt`, `cmake/*.cmake` (or `cache.cmake.inputs`), compiler (CC/CXX), and BOOST_ROOT when Boost cache enabled; path = `cmake/<job_matrix_key>_<digest>`. ✅
- When inputs change, new digest → new directory → workflow runs configure; when unchanged, same dir → workflow can skip configure. ✅
- `localci cache clear --target cmake` clears CMake cache dirs. ✅

**Dependencies:** Issue 10 (per preparation doc). Logically depends on a stable workspace/cache layout so that CMake cache paths remain valid across runs.

**Integration:** Cache dir mounted; `LOCALCI_CMAKE_CACHE_DIR` set in job env. Workflow (or cmake-workflow action) should use it as build dir when set and skip configure when the cache is valid (documented in USER_GUIDE).

**Design reference:** Preparation doc — Bottleneck “CMake Configure: Reconfigures even when unchanged”.

---

## Data Flow and Cache Layout

- **Host cache root:** e.g. `~/.localci/cache/` (or value from `.localci.yml`).
- **Subdirectories:**
  - `ccache/` — build artifact cache (Issue 9).
  - `boost/` — Boost superproject clone (Issue 10); one branch at a time, updated via fetch+reset.
  - `b2-source/<job_matrix_key>/` — per-job `boost-root` tree including `bin.v2/` artifacts (when `cache.boost.build_dir` true); enables incremental b2 builds.
  - `cmake/<job_matrix_key>_<input_digest>/` — CMake cache per job/matrix and input digest (Issue 11); digest changes when CMakeLists.txt, toolchain, compiler, or BOOST_ROOT change.
- **Visibility:** Cache dirs are bind-mounted into the container at `/tmp/localci-cache/{ccache,boost,b2-source,cmake}`. Environment variables: `CCACHE_DIR`, `CCACHE_MAXSIZE`, `CCACHE_COMPRESS`, `BOOST_ROOT`, `LOCALCI_B2_SOURCE_DIR`, `LOCALCI_CMAKE_CACHE_DIR`.

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
    max_size: "5G"              # or "2G"
    compress: true              # CCACHE_COMPRESS
    dir: ~/.localci/cache/ccache

  # Boost dependency cache (Issue 10)
  boost:
    enabled: true
    dir: ~/.localci/cache/boost
    branch: develop             # or master
    shallow: true
    remote: https://github.com/boostorg/boost.git   # optional
    build_dir: true             # cache per-job boost-root (source + bin.v2) via LOCALCI_B2_SOURCE_DIR

  # CMake config cache (Issue 11); path keyed by input digest
  cmake:
    enabled: true
    dir: ~/.localci/cache/cmake
    inputs: [CMakeLists.txt, cmake/*.cmake]   # optional; default for change detection
```

**CLI:** `--no-cache` disables all caches; `--cache-dir <path>` overrides cache root. **Cache commands:** `localci cache clear [--target ccache|boost|cmake|b2-source|all]`, `localci cache stats` (ccache), `localci cache update` (Boost).

---

## Success Criteria (from Preparation Document)

| Metric | Target |
|--------|--------|
| Full Linux CI | &lt; 2 minutes (from 12–15 min) |
| Incremental build | &lt; 30 seconds |
| Single job execution | &lt; 15 seconds (with warm cache) |
| Cache hit rate | &gt; 90% after warm-up |

---

## Verifying incremental builds (B2)

After a small change (e.g. one source file) and a successful `localci run`, you can confirm that only the modified file (and its dependents) were rebuilt instead of the whole project.

1. **Run with verbose output** so the full B2 log is visible:
   ```bash
   localci run -v
   ```
   Or inspect the job log file from a previous run (see `localci run --help` for log location).

2. **In the "Boost B2 Workflow" step log**, B2 prints one line per compilation, e.g.:
   ```text
   clang-linux.compile.c++ bin.v2/libs/capy/build/.../src/detail/thread_name.o
   ```
   - **Incremental:** You see only one or a few `compile.c++` lines (the changed file and anything that depends on it). For a single change in one `.cpp`, expect one such line (and possibly a link step).
   - **Full rebuild:** You see many `compile.c++` lines (dozens or hundreds) for lots of `.o` files.

3. **Quick count** (if the log is in a file):
   ```bash
   grep -c "compile.c++" <path-to-job-log>
   ```
   A small number (e.g. 1–3) means incremental; a large number (e.g. 50+) means a larger or full rebuild.

4. **Timing:** An incremental run after a one-file change should complete the B2 step in well under 30 seconds; a full rebuild takes much longer.

---

## Phase 2 Deliverables Summary

| Component | Status | Scope |
|-----------|--------|-------|
| Build Artifact Cache (Issue 9) | Done | ccache bind mount; CCACHE_DIR, CCACHE_MAXSIZE, CCACHE_COMPRESS; config dir/max_size/compress; ccache stats after run + `localci cache stats`; `localci cache clear` |
| Boost Dependency Cache (Issue 10) | Done | Pre-clone/fetch+reset + submodules in `boost_cache.py`; BOOST_ROOT mount; workflow patched: Clone Boost skipped when BOOST_ROOT set, `cp -rL boost-source boost-root` replaced by persistent per-job `b2-source` cache (rsync + symlink); `LOCALCI_B2_SOURCE_DIR`; `localci cache update`, `localci cache clear --target b2-source` |
| CMake Config Cache (Issue 11) | Done | Per-job dir keyed by input digest (`cmake_cache.compute_cmake_input_digest`); optional `cache.cmake.inputs`; LOCALCI_CMAKE_CACHE_DIR; `localci cache clear --target cmake` |
| Cache config in .localci.yml | Done | cache.ccache (dir, max_size, compress), cache.boost (dir, branch, shallow, remote, build_dir), cache.cmake (dir, inputs); --no-cache, --cache-dir |
| Documentation | Done | USER_GUIDE.md cache section (ccache, boost, b2-source, cmake; change detection; invalidation; `localci cache` clear/stats/update) |

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
3. ~~ccache hit/miss reporting~~ — Done: stats after run and `localci cache stats`.
4. Workflow patch: Local CI patches the workflow (a) Clone Boost skipped when `BOOST_ROOT` set + "Use cached Boost" step added; (b) `cp -rL boost-source boost-root` replaced with persistent per-job `boost-root` from `b2-source` cache (rsync updates only changed files, preserves `bin.v2/`); cmake-workflow should use `LOCALCI_CMAKE_CACHE_DIR` to skip reconfigure.

---

## Reference

- **Preparation document:** `2026-02/2026-02-06/brad/Local CI sytem for capy preparation.md` — Child Issues Breakdown (Phase 3: Caching and Optimization = Issues 9, 10, 11), Implementation Priority Order (Sprint 2: Performance).
- **Phase 1 summary:** `2026-02/2026-02-06/brad/Local CI Phase 1 - Core Infrastructure.md` — Foundation that Phase 2 builds on.
