Local CI System for Capy - Preparation and Issue Breakdown


## Summary

Analyzed the Local CI System Design document and capy's GitHub Actions workflow to create a comprehensive implementation plan. The goal is to reduce CI execution time from **12-15 minutes to ~1 minute** by implementing a local CI system with aggressive caching and parallel execution.

## Current State Analysis

### Capy CI Metrics

| Metric | Current | Target |
|--------|---------|--------|
| CI workflow time | 12-15 min | ~1 min |
| Documentation workflow | ~1 min | ~30 sec |
| Code Coverage | ~3 min | ~1 min |
| Matrix configurations | 14 total | Focus on Linux first |

### Capy CI Matrix Breakdown
- **Windows**: 3 configurations (MSVC 14.42, MSVC 14.34, MinGW)
- **macOS**: 1 configuration (Apple-Clang, asan+ubsan)
- **Linux**: 10 configurations
  - GCC: 12, 13, 15 (including asan/ubsan/coverage)
  - Clang: 17, 20 (including asan/ubsan, x86)

### Bottlenecks Identified
1. **Boost Clone** - Clones entire Boost superproject every run
2. **B2 Build** - Full build from scratch each time
3. **CMake Configure** - Reconfigures even when unchanged
4. **No Caching** - Every run starts fresh
5. **Sequential Dependencies** - Jobs wait unnecessarily

---

## Architecture Overview (From [Design Doc](Design%20Guide.md))

```text
┌─────────────────────────────────────────────────────────────────┐
│                      MCP Server Interface                        │
│  analyze_workflow | run_local_ci | get_status | get_logs        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    Test Orchestrator                             │
│  • Priority-based queue                                          │
│  • Parallel execution (~20 jobs)                                 │
│  • Progress tracking                                             │
└─────────────────────┬───────────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ CI Workflow   │ │ Image Mgmt    │ │ Job Executor  │
│ Analyzer      │ │ System        │ │ (act)         │
│ (yq)          │ │ (Docker)      │ │               │
└───────────────┘ └───────────────┘ └───────────────┘
```

---

## Child Issues Breakdown

### Phase 1: Core Infrastructure (Linux First) - Priority: High

#### Issue 1: CLI Framework and Command Parser
**Scope**: Create RWX-style CLI with fine-grained commands
**Deliverables**:
- CLI entry point with subcommands
- Command: `localci analyze <workflow.yml>` - Parse and display jobs/matrix
- Command: `localci list` - List available jobs and matrix entries
- Command: `localci run [options]` - Execute selected jobs
- Command: `localci status` - Show execution progress
- Command: `localci logs <job>` - View job logs
- Configuration file support (`.localci.yml`)
**Dependencies**: None
**Estimate**: Core framework

#### Issue 2: Workflow Analyzer Module
**Scope**: Parse GitHub Actions YAML files using yq
**Deliverables**:
- Extract jobs, matrix configurations, dependencies
- Identify OS/container requirements
- Extract compiler versions and packages
- Support for `push`, `pull_request` events
- Output structured JSON for downstream processing
**Dependencies**: Issue 1
**Estimate**: Analysis engine

#### Issue 3: Image Registry and Matching Algorithm
**Scope**: Implement two-mark image matching system
**Deliverables**:
- Image registry YAML schema (`image-registry.yml`)
- Essential marks calculation (OS, architecture, compiler)
- Extra marks calculation (packages, tools)
- Image selection algorithm
- Registry CRUD operations
**Dependencies**: Issue 2
**Estimate**: Matching algorithm

#### Issue 4: Docker Image Management
**Scope**: Pre-built image loading and creation
**Deliverables**:
- Load images from `.tar` files
- Create new images when no match found
- Save newly built images for reuse
- Image naming convention enforcement
- Disk space management (cleanup old images)
**Dependencies**: Issue 3
**Estimate**: Docker integration

#### Issue 5: Job Executor with `act` Integration
**Scope**: Execute GitHub Actions locally via act
**Deliverables**:
- Act command builder (flags, matrix filters)
- Container lifecycle management
- stdout/stderr capture
- Exit code handling
- Result aggregation
**Dependencies**: Issue 4
**Estimate**: Execution engine

---

### Phase 2: Parallel Execution and Orchestration - Priority: High

#### Issue 6: Priority-Based Job Queue
**Scope**: Manage job execution order with priorities
**Deliverables**:
- Priority queue implementation
- Dependency resolution
- Priority extraction from config
- Queue state management
**Dependencies**: Issue 5
**Estimate**: Queue system

#### Issue 7: Parallel Execution Manager
**Scope**: Run multiple jobs concurrently
**Deliverables**:
- Configurable parallelism limit (~20 jobs)
- Resource monitoring (CPU, memory, disk)
- Priority-based scheduling
- Completion handling and next-job dispatch
**Dependencies**: Issue 6
**Estimate**: Parallel manager

#### Issue 8: Real-time Progress Tracking
**Scope**: Monitor and report execution progress
**Deliverables**:
- Progress aggregation (X/Y jobs completed)
- Per-job status (pending, running, completed, failed)
- Terminal UI with live updates
- Summary report generation
**Dependencies**: Issue 7
**Estimate**: Progress UI

---

### Phase 3: Caching and Optimization - Priority: Critical

#### Issue 9: Build Artifact Caching (ccache/sccache)
**Scope**: Cache compilation artifacts across runs
**Deliverables**:
- ccache/sccache integration
- Cache directory management
- Cache hit/miss reporting
- Cache invalidation strategy
**Dependencies**: Issue 5
**Estimate**: Build caching

#### Issue 10: Boost Dependency Caching
**Scope**: Pre-clone and cache Boost superproject
**Deliverables**:
- One-time Boost clone/update
- Shallow clone support
- Branch-specific caching (develop, master)
- Incremental updates only
**Dependencies**: Issue 9
**Estimate**: Dependency caching

#### Issue 11: CMake Configuration Caching
**Scope**: Skip configure when unchanged
**Deliverables**:
- CMake cache persistence
- Change detection (CMakeLists.txt, toolchain)
- Incremental reconfiguration
**Dependencies**: Issue 10
**Estimate**: CMake caching

---

### Phase 4: Pre-built Images for Capy - Priority: High

#### Issue 12: Linux Base Images (Ubuntu)
**Scope**: Create pre-built Docker images for Linux CI
**Deliverables**:
- `capy-ubuntu-24.04-gcc13.tar` (coverage)
- `capy-ubuntu-24.04-clang17.tar`
- `capy-ubuntu-24.04-clang20.tar`
- `capy-ubuntu-25.04-gcc15.tar`
- `capy-ubuntu-25.04-gcc15-asan.tar`
- `capy-ubuntu-25.04-clang20.tar`
- `capy-ubuntu-25.04-clang20-asan.tar`
- `capy-ubuntu-25.04-clang20-x86.tar`
- Pre-installed: Boost dependencies, cmake, ccache
**Dependencies**: Issue 4
**Estimate**: Image building

---

### Phase 5: Platform Support - Priority: Medium

#### Issue 13: Windows Support (MSVC/MinGW)
**Scope**: Enable Windows container execution
**Deliverables**:
- Windows container support
- MSVC toolchain images
- MinGW images
- Windows-specific path handling
**Dependencies**: Issues 1-8
**Estimate**: Windows platform

#### Issue 14: macOS Support (Limitations)
**Scope**: Document macOS limitations and workarounds
**Deliverables**:
- Document: macOS not containerizable
- Alternative: SSH to macOS host for testing
- Skip macOS in local CI with clear messaging
**Dependencies**: None
**Estimate**: Documentation only

---

### Phase 6: MCP Integration - Priority: Medium

#### Issue 15: MCP Server Endpoints
**Scope**: Expose local CI via MCP for AI agent integration
**Deliverables**:
- `analyze_workflow` endpoint
- `run_local_ci` endpoint
- `get_status` endpoint
- `get_logs` endpoint
- Async operation support
**Dependencies**: Issues 1-8
**Estimate**: MCP integration

---

### Phase 7: Developer Experience - Priority: Low

#### Issue 16: Configuration File Support
**Scope**: Project-specific configuration
**Deliverables**:
- `.localci.yml` schema
- Job filters
- Matrix filters
- Priority overrides
- Default parallelism
**Dependencies**: Issue 1
**Estimate**: Config system

#### Issue 17: IDE Integration (VS Code/Cursor)
**Scope**: Integrate with developer IDEs
**Deliverables**:
- VS Code extension or tasks.json templates
- Cursor command integration
- One-click test execution
**Dependencies**: Issues 1-8
**Estimate**: IDE integration

---

## Implementation Priority Order

### Sprint 1: Foundation (Linux First)
1. Issue 1: CLI Framework
2. Issue 2: Workflow Analyzer
3. Issue 5: Job Executor with act
4. Issue 12: Linux Base Images

### Sprint 2: Performance
5. Issue 9: Build Artifact Caching
6. Issue 10: Boost Dependency Caching
7. Issue 11: CMake Configuration Caching

### Sprint 3: Parallel Execution
8. Issue 3: Image Registry
9. Issue 4: Docker Image Management
10. Issue 6: Priority Queue
11. Issue 7: Parallel Execution Manager
12. Issue 8: Progress Tracking

### Sprint 4: Platform Expansion
13. Issue 13: Windows Support
14. Issue 15: MCP Server Endpoints
15. Issue 16: Configuration File Support

---

## Coordination with RWX

Reference RWX implementation for:
- CLI design patterns
- Caching strategies
- Parallel execution approach
- Progress reporting

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Full Linux CI | < 2 minutes (from 12-15 min) |
| Incremental build | < 30 seconds |
| Single job execution | < 15 seconds |
| Cache hit rate | > 90% after warm-up |

---
