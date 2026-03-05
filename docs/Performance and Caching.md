# Performance and Caching — Feature Guide

This document describes Local CI’s **caching and performance features** in detail: what each cache does, how it works, how to configure it, and how to verify it is being used.

**See also:** [User Guide](../cli/USER_GUIDE.md) for installation and day-to-day usage; [Design Guide](Design%20Guide.md) and [Preparation and Plan](Preparation%20and%20Plan.md) for development and implementation plan.

---

## Overview

Local CI speeds up repeated runs by caching:

- **Compilation output** (ccache) so unchanged source files are not recompiled.
- **Boost superproject** so the repo is cloned/updated once and reused.
- **B2 build tree** (per-job) so Boost.Build (b2) only rebuilds what changed.
- **CMake configuration** (per job + input digest) so configure is skipped when inputs are unchanged.
- **APT package archives** so the “Install packages” step reuses downloaded `.deb` files.

With caches warm, full Linux CI can complete in under ~2 minutes and incremental builds in under ~30 seconds.

---

## How Caching Fits In

```text
┌─────────────────────────────────────────────────────────────────┐
│                      MCP / CLI                                    │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    Job Executor (act)                             │
│                    Linux Base Images                             │
└─────────────────────┬───────────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ Build         │ │ Boost         │ │ CMake         │
│ Artifact      │ │ Dependency    │ │ Config        │
│ Cache         │ │ Cache         │ │ Cache         │
│ (ccache)      │ │ + b2-source   │ │ Cache         │
└───────────────┘ └───────────────┘ └───────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                 ┌─────────────────┐
                 │ Host cache dirs  │
                 │ (~/.localci/    │
                 │  cache/...)     │
                 └─────────────────┘
```

Cache directories on the host are bind-mounted into the job container so that compilers, B2, CMake, and apt see persistent data across runs.

---

## Feature 1: Build Artifact Cache (ccache)

### What it does

Stores compiled object files and metadata so that if you re-run the same job without changing source code, the compiler step is skipped or greatly reduced. This applies to both B2 and CMake builds.

### How it works

- A single shared directory (e.g. `~/.localci/cache/ccache`) is mounted into every job container.
- Local CI sets `CCACHE_DIR` and wraps compilers with ccache: `CC=ccache gcc`, `CXX=ccache g++` (or the matrix compiler). B2 and CMake then call these wrappers and ccache stores/retrieves results by content hash.
- First run: compilations run as usual and populate the cache. Later runs: cache hits for unchanged files, so only changed files (and their dependents) are recompiled.

### Configuration

In `.localci.yml`:

```yaml
cache:
  enabled: true
  ccache:
    enabled: true
    max_size: "5G"    # ccache size limit
    compress: true   # CCACHE_COMPRESS
    dir: ~/.localci/cache/ccache   # optional; default under cache.directory
```

### CLI

- **Disable for a run:** `localci run --no-cache`
- **View stats:** `localci cache stats` (runs `ccache -s` on the configured dir)
- **Clear:** `localci cache clear --target ccache` (or `--target all`)

### Verifying

After a run, `localci cache stats` shows hit/miss counts. After an incremental change, you should see many cache hits and only a few compilations in the B2/CMake logs.

---

## Feature 2: Boost Dependency Cache

### What it does

Keeps a single clone of the Boost superproject (e.g. `develop` or `master`) on the host. Jobs use this instead of cloning Boost inside the container every time, and they can update it incrementally with `git fetch` + `git reset`.

### How it works

- Local CI (or you via `localci cache update`) clones Boost once into e.g. `~/.localci/cache/boost` and runs `git submodule update --init --recursive`. The workflow is patched so that when `BOOST_ROOT` is set (the mount path), the “Clone Boost” step is skipped and a “Use cached Boost” step creates `boost-source` from the cache.
- The cache is branch-specific: one directory per branch (e.g. `develop`). Refreshing is done with `git fetch` and `git reset --hard origin/<branch>` so you get the latest commits without a full clone.

### Configuration

```yaml
cache:
  boost:
    enabled: true
    dir: ~/.localci/cache/boost
    branch: develop
    shallow: true
    remote: https://github.com/boostorg/boost.git
    build_dir: true   # enables b2-source per-job cache (see below)
```

### CLI

- **Refresh Boost only:** `localci cache update` (no need to run full CI)
- **Clear:** `localci cache clear --target boost`

---

## Feature 3: B2 Source and Build Artifacts Cache (b2-source)

### What it does

Persists the **per-job** `boost-root` tree (Boost source plus B2’s `bin.v2` and the b2 binary) so that B2 sees stable timestamps and only rebuilds files that actually changed. This is the main enabler of incremental B2 builds.

### How it works

- For each matrix job, Local CI reserves a directory like `~/.localci/cache/b2-source/<job_matrix_key>` and mount it at `LOCALCI_B2_SOURCE_DIR` in the container.
- The workflow is patched: instead of always doing `cp -rL boost-source boost-root`, on cache hit the job uses the existing tree (only `libs/<module>` is cleared and replaced with the current repo copy), and restores file timestamps for capy sources from a saved snapshot so B2’s change detection is correct. On first run it does the usual copy and seeds the cache.
- B2 bootstrap is also short-circuited when the cached tree already contains the `b2` binary, saving ~20s per job.

### Configuration

Controlled by `cache.boost.build_dir` (default `true`). When true, the b2-source paths are derived from `cache.directory` and the job/matrix key.

### CLI

- **Clear:** `localci cache clear --target b2-source`

### Verifying incremental B2 builds

1. Run with verbose output: `localci run -v`, or open the job log from the run.
2. In the “Boost B2 Workflow” step, B2 prints one line per compilation (e.g. `compile.c++ ... thread_name.o`). After a one-file change you should see only one or a few such lines; a full rebuild shows many.
3. Count compilations: `grep -c "compile.c++" <path-to-job-log>`. A small number (e.g. 1–3) means incremental; 50+ suggests a larger or full rebuild.
4. The B2 step for an incremental change should finish in well under 30 seconds.

---

## Feature 4: CMake Configuration Cache

### What it does

Stores the CMake build directory (configure + build tree) per job and per “input digest.” When CMakeLists.txt, toolchain, compiler, or BOOST_ROOT do not change, the workflow can reuse the same directory and skip configure.

### How it works

- Local CI computes a digest from configured inputs (e.g. `CMakeLists.txt`, `cmake/*.cmake`, compiler, BOOST_ROOT). The cache path is `~/.localci/cache/cmake/<job_matrix_key>_<digest>`.
- The directory is bind-mounted and `LOCALCI_CMAKE_CACHE_DIR` is set. The workflow (or cmake-workflow action) should use this as the build directory when set; then configure runs only when the digest changes (e.g. after editing CMakeLists.txt or changing compiler).

### Configuration

```yaml
cache:
  cmake:
    enabled: true
    dir: ~/.localci/cache/cmake
    inputs: [CMakeLists.txt, cmake/*.cmake]   # optional; used for digest
```

### CLI

- **Clear:** `localci cache clear --target cmake`

---

## Feature 5: APT Package Install Cache

### What it does

Speeds up the workflow’s **“Install packages”** step (e.g. `apt-get install` or the `package-install` action) by reusing downloaded `.deb` files across runs instead of downloading them again in every container.

### How it works

- A per-job directory (e.g. `~/.localci/cache/apt/<job_matrix_key>`) is bind-mounted over the container’s `/var/cache/apt/archives`. The first run downloads packages into this dir; subsequent runs for the same job use the same dir, so `apt-get install` finds the packages locally and runs much faster.

### Configuration

```yaml
cache:
  apt:
    enabled: true
    dir: ~/.localci/cache/apt
```

### CLI

- **Clear:** `localci cache clear --target apt`

---

## Cache Layout on Disk

- **Cache root:** `~/.localci/cache/` (or `cache.directory` in `.localci.yml`).
- **Subdirectories:**
  - `ccache/` — shared compilation cache.
  - `boost/` — Boost superproject clone (one branch at a time).
  - `b2-source/<job_matrix_key>/` — per-job boost-root + bin.v2.
  - `cmake/<job_matrix_key>_<input_digest>/` — per-job, input-keyed CMake build dir.
  - `apt/<job_matrix_key>/` — per-job APT archives.
- **In the container:** ccache, boost, b2-source, and cmake are under `/tmp/localci-cache/`; apt is mounted at `/var/cache/apt/archives`. Environment variables set by Local CI: `CCACHE_DIR`, `CCACHE_MAXSIZE`, `CCACHE_COMPRESS`, `BOOST_ROOT`, `LOCALCI_B2_SOURCE_DIR`, `LOCALCI_CMAKE_CACHE_DIR`.

---

## Global Cache Options

- **Disable all caches for a run:** `localci run --no-cache`
- **Override cache root:** `localci run --cache-dir /path/to/cache`
- **Clear one or all:** `localci cache clear --target ccache|boost|cmake|b2-source|apt|all` (optionally with `--yes` to skip confirmation)

---

## Success Criteria (Targets)

| Metric            | Target                          |
|-------------------|----------------------------------|
| Full Linux CI     | &lt; 2 minutes (with warm cache) |
| Incremental build | &lt; 30 seconds                 |
| Single job        | &lt; 15 seconds (warm cache)    |
| Cache hit rate    | &gt; 90% after warm-up          |

---

## Reference

- Implementation plan and issue breakdown: [Preparation and Plan](Preparation%20and%20Plan.md).
- Architecture and MCP: [Design Guide](Design%20Guide.md).
- Config reference and troubleshooting: [User Guide](../cli/USER_GUIDE.md).
