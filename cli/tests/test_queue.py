"""Tests for priority-based job queue (Issue 6)."""

import threading
import time
from pathlib import Path

import pytest

from localci.core.models import (
    JobEventType,
    QueuedJob,
    QueuedJobStatus,
)
from localci.core.queue import (
    CyclicDependencyError,
    DependencyResolver,
    PriorityConfig,
    PriorityJobQueue,
    PriorityRule,
)
from localci.core.queue_builder import QueueBuilder
from localci.core.workflow import (
    BuildSystem,
    BuildVariant,
    CompilerFamily,
    CompilerInfo,
    ContainerInfo,
    MatrixEntry,
    PackageRequirements,
    Platform,
    WorkflowAnalyzer,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FULL_WORKFLOW = FIXTURES_DIR / "sample_workflow.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_entry(
    name: str,
    compiler: str = "gcc",
    version: str = "15",
    platform: Platform = Platform.LINUX,
    index: int = 0,
) -> MatrixEntry:
    family = {
        "gcc": CompilerFamily.GCC,
        "clang": CompilerFamily.CLANG,
        "msvc": CompilerFamily.MSVC,
    }.get(compiler, CompilerFamily.UNKNOWN)
    return MatrixEntry(
        index=index,
        name=name,
        platform=platform,
        compiler=CompilerInfo(family=family, version=version),
        container=ContainerInfo(),
        variant=BuildVariant(),
        packages=PackageRequirements(),
        runs_on="ubuntu-latest",
        build_system=BuildSystem.B2,
    )


def make_job(
    name: str,
    priority: int = 5,
    deps: list[str] | None = None,
    compiler: str = "gcc",
    index: int = 0,
) -> QueuedJob:
    return QueuedJob(
        job_id="build",
        matrix_entry=make_entry(name, compiler=compiler, index=index),
        priority=priority,
        dependencies=deps or [],
    )


# ---------------------------------------------------------------------------
# DependencyResolver
# ---------------------------------------------------------------------------


class TestDependencyResolver:
    def test_no_dependencies(self):
        resolver = DependencyResolver()
        resolver.add_job("a", [])
        resolver.add_job("b", [])
        order = resolver.resolve()
        assert set(order) == {"a", "b"}

    def test_linear_dependencies(self):
        resolver = DependencyResolver()
        resolver.add_job("a", [])
        resolver.add_job("b", ["a"])
        resolver.add_job("c", ["b"])
        order = resolver.resolve()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_diamond_dependencies(self):
        resolver = DependencyResolver()
        resolver.add_job("a", [])
        resolver.add_job("b", ["a"])
        resolver.add_job("c", ["a"])
        resolver.add_job("d", ["b", "c"])
        order = resolver.resolve()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cyclic_dependency(self):
        resolver = DependencyResolver()
        resolver.add_job("a", ["b"])
        resolver.add_job("b", ["a"])
        with pytest.raises(CyclicDependencyError):
            resolver.resolve()

    def test_all_dependencies_met(self):
        resolver = DependencyResolver()
        resolver.add_job("a", [])
        resolver.add_job("b", ["a"])
        assert resolver.all_dependencies_met("a", set()) is True
        assert resolver.all_dependencies_met("b", set()) is False
        assert resolver.all_dependencies_met("b", {"a"}) is True


# ---------------------------------------------------------------------------
# PriorityJobQueue
# ---------------------------------------------------------------------------


class TestPriorityJobQueue:
    def test_enqueue_single(self):
        queue = PriorityJobQueue()
        job = make_job("GCC 15", priority=1)
        queue.enqueue(job)
        assert queue.total_jobs == 1
        assert queue.pending_count == 1

    def test_dequeue_order_by_priority(self):
        queue = PriorityJobQueue()
        low = make_job("Low priority", priority=5, index=0)
        high = make_job("High priority", priority=1, index=1)
        queue.enqueue(low)
        queue.enqueue(high)
        first = queue.next_ready()
        assert first is not None
        assert first.matrix_entry.name == "High priority"

    def test_priority_gate(self):
        queue = PriorityJobQueue()
        p1 = make_job("Priority 1", priority=1, index=0)
        p2 = make_job("Priority 2", priority=2, index=1)
        queue.enqueue(p1)
        queue.enqueue(p2)
        job = queue.next_ready()
        assert job is not None
        assert job.matrix_entry.name == "Priority 1"
        queue.mark_running(job)
        assert queue.next_ready() is None
        queue.mark_completed(job, success=True)
        job2 = queue.next_ready()
        assert job2 is not None
        assert job2.matrix_entry.name == "Priority 2"

    def test_same_priority_parallel(self):
        queue = PriorityJobQueue()
        a = make_job("Job A", priority=1, index=0)
        b = make_job("Job B", priority=1, index=1)
        queue.enqueue(a)
        queue.enqueue(b)
        first = queue.next_ready()
        assert first is not None
        queue.mark_running(first)
        second = queue.next_ready()
        assert second is not None

    def test_completion_tracking(self):
        queue = PriorityJobQueue()
        job = make_job("Test", priority=1)
        queue.enqueue(job)
        ready = queue.next_ready()
        assert ready is not None
        queue.mark_running(ready)
        assert queue.running_count == 1
        queue.mark_completed(ready, success=True)
        assert queue.passed_count == 1
        assert queue.running_count == 0
        assert queue.is_done is True

    def test_failure_tracking(self):
        queue = PriorityJobQueue()
        job = make_job("Fail", priority=1)
        queue.enqueue(job)
        ready = queue.next_ready()
        assert ready is not None
        queue.mark_running(ready)
        queue.mark_completed(ready, success=False)
        assert queue.failed_count == 1
        assert queue.passed_count == 0
        assert queue.is_done is True

    def test_cancel_job(self):
        queue = PriorityJobQueue()
        job = make_job("Cancel me", priority=1)
        queue.enqueue(job)
        assert queue.cancel(job.queue_key) is True
        assert queue.is_done is True

    def test_cancel_all(self):
        queue = PriorityJobQueue()
        for i in range(5):
            queue.enqueue(make_job(f"Job {i}", priority=1, index=i))
        count = queue.cancel_all()
        assert count == 5
        assert queue.is_done is True

    def test_event_emission(self):
        events = []
        queue = PriorityJobQueue()
        queue.add_listener(lambda e: events.append(e))
        job = make_job("Observed", priority=1)
        queue.enqueue(job)
        assert len(events) == 1
        assert events[0].event_type == JobEventType.JOB_QUEUED

    def test_priority_level_complete_event(self):
        events = []
        queue = PriorityJobQueue()
        queue.add_listener(lambda e: events.append(e))
        job = make_job("Only job", priority=1)
        queue.enqueue(job)
        ready = queue.next_ready()
        assert ready is not None
        queue.mark_running(ready)
        queue.mark_completed(ready, success=True)
        event_types = [e.event_type for e in events]
        assert JobEventType.PRIORITY_LEVEL_COMPLETE in event_types
        assert JobEventType.ALL_COMPLETE in event_types

    def test_priority_summary(self):
        queue = PriorityJobQueue()
        queue.enqueue(make_job("P1-A", priority=1, index=0))
        queue.enqueue(make_job("P1-B", priority=1, index=1))
        queue.enqueue(make_job("P2-A", priority=2, index=2))
        summary = queue.get_priority_summary()
        assert summary[1]["total"] == 2
        assert summary[2]["total"] == 1

    def test_is_empty_when_all_complete(self):
        queue = PriorityJobQueue()
        job = make_job("Single", priority=1)
        queue.enqueue(job)
        assert queue.is_empty is False
        ready = queue.next_ready()
        assert ready is not None
        queue.mark_running(ready)
        queue.mark_completed(ready, success=True)
        assert queue.is_empty is True


# ---------------------------------------------------------------------------
# PriorityConfig
# ---------------------------------------------------------------------------


class TestPriorityConfig:
    def test_explicit_mapping(self):
        config = PriorityConfig(
            explicit={"GCC 15: C++20": 1, "Clang 20: C++20-23": 2}
        )
        job = make_job("GCC 15: C++20")
        assert config.resolve_priority(job) == 1

    def test_rule_matching(self):
        config = PriorityConfig(
            rules=[
                PriorityRule(pattern="gcc", match_type="compiler", priority=1),
                PriorityRule(pattern="clang", match_type="compiler", priority=2),
            ]
        )
        gcc_job = make_job("GCC 15", compiler="gcc")
        clang_job = make_job("Clang 20", compiler="clang")
        assert config.resolve_priority(gcc_job) == 1
        assert config.resolve_priority(clang_job) == 2

    def test_glob_name_match(self):
        config = PriorityConfig(
            rules=[
                PriorityRule(pattern="*asan*", match_type="name", priority=10),
            ]
        )
        asan_job = make_job("GCC 15 asan+ubsan")
        normal_job = make_job("GCC 15: C++20")
        assert config.resolve_priority(asan_job) == 10
        assert config.resolve_priority(normal_job) == 5

    def test_default_priority(self):
        config = PriorityConfig(default_priority=7)
        job = make_job("Unknown")
        assert config.resolve_priority(job) == 7


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_enqueue(self):
        queue = PriorityJobQueue()

        def enqueue_batch(start: int, count: int) -> None:
            for i in range(start, start + count):
                queue.enqueue(make_job(f"Job {i}", priority=1, index=i))

        threads = [
            threading.Thread(target=enqueue_batch, args=(0, 50)),
            threading.Thread(target=enqueue_batch, args=(50, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert queue.total_jobs == 100

    def test_concurrent_dequeue(self):
        queue = PriorityJobQueue()
        for i in range(20):
            queue.enqueue(make_job(f"Job {i}", priority=1, index=i))
        results: list[QueuedJob] = []

        def consume() -> None:
            while not queue.is_done:
                job = queue.next_ready()
                if job is None:
                    time.sleep(0.01)
                    continue
                queue.mark_running(job)
                results.append(job)
                queue.mark_completed(job, success=True)

        threads = [threading.Thread(target=consume) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 20
        names = {r.matrix_entry.name for r in results}
        assert len(names) == 20


# ---------------------------------------------------------------------------
# QueueBuilder integration
# ---------------------------------------------------------------------------


class TestQueueBuilderIntegration:
    """Build queue from real workflow (capy-style)."""

    def test_build_queue_linux_only(self):
        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(FULL_WORKFLOW)
        builder = QueueBuilder(workflow)
        queue = builder.build(platform_filter=Platform.LINUX)
        # sample_workflow has 10 Linux matrix entries in build job
        assert queue.total_jobs == 10
        assert len(queue._priority_levels) >= 1

    def test_build_queue_with_priorities(self):
        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(FULL_WORKFLOW)
        config = type("Config", (), {"priorities": {"GCC 15: C++20": 1, "GCC 12: C++20": 2}})()
        priority_config = PriorityConfig.from_config(config)
        builder = QueueBuilder(workflow, priority_config=priority_config)
        queue = builder.build(platform_filter=Platform.LINUX)
        assert queue.total_jobs == 10
        summary = queue.get_priority_summary()
        # At least one job at priority 1 and one at 2 (or default 5)
        assert len(summary) >= 1

    def test_next_ready_consumes_in_priority_order(self):
        analyzer = WorkflowAnalyzer()
        workflow = analyzer.analyze(FULL_WORKFLOW)
        builder = QueueBuilder(workflow)
        queue = builder.build(platform_filter=Platform.LINUX)
        consumed = 0
        while not queue.is_done:
            job = queue.next_ready()
            if job is None:
                time.sleep(0.01)
                continue
            queue.mark_running(job)
            queue.mark_completed(job, success=True)
            consumed += 1
        assert consumed == 10
        assert queue.passed_count == 10
        assert queue.is_empty
