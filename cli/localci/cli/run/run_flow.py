"""Testable orchestration logic for ``localci run`` (not ``localci.core.orchestrator``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from localci.cli.run.container import RunDependencies, build_run_container
from localci.cli.run.params import RunOptions
from localci.cli.run.patcher import _print_execution_plan, make_workflow_patcher
from localci.core.config import LocalCIConfig
from localci.core.executor import JobExecutor
from localci.core.github_token import resolve_github_token, warn_sentinel_github_token
from localci.core.models import JobEvent, JobEventType
from localci.core.results import ExecutionSummary
from localci.core.workflow import PLATFORM_CLI_MAP, MatrixEntry, Workflow
from localci.errors import ActNotFoundError, DockerNotAvailableError, WorkflowError
from localci.utils.output import print_error, print_info, print_warning
from localci.utils.paths import REGISTRY_FILENAME, find_file_upward


def execute_run(
    *,
    cfg: LocalCIConfig,
    options: RunOptions,
    deps: RunDependencies | None = None,
) -> int:
    """Run the full ``localci run`` flow; return process exit code (0 = success)."""
    container = deps or build_run_container()

    effective_timeout = options.timeout or cfg.execution.timeout
    effective_parallel = options.parallel or cfg.parallel.max_jobs
    effective_keep_containers = (
        options.keep_containers
        if options.keep_containers is not None
        else cfg.execution.keep_containers
    )
    workflow_path = Path(options.workflow) if options.workflow else cfg.workflow
    project_dir = Path(".").resolve()
    gh_token = resolve_github_token(options.github_token)
    if not options.offline:
        warn_sentinel_github_token(gh_token)

    try:
        wf = container.workflow_analyzer.analyze(workflow_path)
    except WorkflowError as exc:
        print_error(str(exc))
        return 1

    all_pairs = _collect_matrix_pairs(wf)
    if not all_pairs:
        print_warning("No matrix entries found in workflow.")
        return 0

    if options.rebuild_image:
        print_warning("--rebuild-image is not yet implemented; ignoring.")
    if options.interactive:
        print_warning("--interactive is not yet implemented; ignoring.")

    selected = _filter_matrix_pairs(
        all_pairs,
        platform=options.platform,
        compiler=options.compiler,
        jobs=options.jobs,
    )
    if not selected:
        print_warning("No jobs match the given filters.")
        return 0

    priority_config = container.priority_config_factory(cfg)
    registry_path = find_file_upward(REGISTRY_FILENAME, project_dir)

    matrix_include, matrix_exclude = _resolve_matrix_filters(options, cfg)
    plat_filter = PLATFORM_CLI_MAP.get(options.platform) if options.platform else None
    compiler_filter = options.compiler.lower() if options.compiler else None
    selected_set = {(jid, e.index) for jid, e in selected}
    job_filter_list = list({jid for jid, _ in selected})

    builder = container.queue_builder_factory(wf, priority_config=priority_config)
    queue = builder.build(
        platform_filter=plat_filter,
        job_filter=job_filter_list,
        compiler_filter=compiler_filter,
        matrix_include=matrix_include,
        matrix_exclude=matrix_exclude,
        entries_include=selected_set,
        registry_path=registry_path,
        platform_config=cfg.platforms,
        native_image_prefix=cfg.project.native_image_prefix,
    )

    if options.dry_run:
        _print_execution_plan(queue, workflow_path, effective_timeout)
        return 0

    logs_dir = Path(cfg.logging.directory)
    executor = container.job_executor_factory(logs_dir)
    preflight_code = _run_preflight(executor)
    if preflight_code != 0:
        return preflight_code

    if (
        not options.no_cache
        and cfg.cache.enabled
        and cfg.cache.boost.enabled
        and not container.ensure_boost_cache_fn(
            cfg.cache, options.no_cache, options.cache_dir
        )
    ):
        print_warning("Boost cache setup failed; jobs will clone Boost from scratch.")

    orch_config = container.orchestrator_config_factory(cfg)
    orch_config.max_parallel = effective_parallel
    orch_config.job_timeout = effective_timeout
    orch_config.keep_containers = effective_keep_containers
    orch_config.default_secrets = {"GITHUB_TOKEN": gh_token}
    orch_config.default_env = {"DEBIAN_FRONTEND": "noninteractive"}
    orch_config.image_registry_path = cfg.images.registry
    orch_config.verbose = options.verbose
    orch_config.offline = options.offline
    orch_config.auto_build = cfg.images.auto_build

    workflow_patcher = make_workflow_patcher(cfg)

    parallel_manager = container.parallel_manager_factory(
        queue=queue,
        workflow_file=workflow_path,
        project_dir=project_dir,
        config=orch_config,
        logs_dir=logs_dir,
        workflow_patcher=workflow_patcher,
        cache_config=cfg.cache,
        no_cache=options.no_cache,
        cache_dir_override=options.cache_dir,
    )

    status_file = logs_dir / "last-status.json"
    tracker = container.progress_tracker_factory(
        queue=queue,
        workflow_file=str(workflow_path),
        platform=options.platform or "linux",
        max_parallel=effective_parallel,
        status_file=status_file,
    )
    for job in queue.get_all_jobs():
        tracker.on_event(JobEvent(event_type=JobEventType.JOB_QUEUED, job=job))
    parallel_manager.add_listener(tracker.on_event)

    _print_cache_enabled_message(cfg, options.no_cache)

    tracker.start_live()
    try:
        run_result = parallel_manager.execute()
    finally:
        tracker.stop_live()

    tracker.set_execution_id(run_result.execution_id)
    tracker.write_status_file()

    summary = ExecutionSummary(
        execution_id=run_result.execution_id,
        started_at=run_result.started_at,
        finished_at=run_result.finished_at,
        results=list(run_result.results.values()),
    )
    tracker.print_summary(run_result)

    _print_ccache_stats(container, cfg, options)

    _save_execution_results(summary, cfg)

    return 0 if summary.all_passed else 1


def _collect_matrix_pairs(wf: Workflow) -> list[tuple[str, MatrixEntry]]:
    pairs: list[tuple[str, MatrixEntry]] = []
    for job_id, job in wf.jobs.items():
        for entry in job.matrix:
            pairs.append((job_id, entry))
    return pairs


def _filter_matrix_pairs(
    all_pairs: list[tuple[str, MatrixEntry]],
    *,
    platform: str | None,
    compiler: str | None,
    jobs: tuple[str, ...],
) -> list[tuple[str, MatrixEntry]]:
    selected = list(all_pairs)
    if platform:
        target_plat = PLATFORM_CLI_MAP.get(platform)
        selected = [(jid, e) for jid, e in selected if e.platform == target_plat]

    compiler_filter = compiler.lower() if compiler else None
    if compiler_filter:
        selected = [
            (jid, e)
            for jid, e in selected
            if e.compiler.family.value == compiler_filter
        ]

    if jobs:
        seen: set[tuple[str, int]] = set()
        filtered_list: list[tuple[str, MatrixEntry]] = []
        for j in jobs:
            try:
                idx = int(j)
                for jid, e in selected:
                    if e.index == idx and (jid, e.index) not in seen:
                        filtered_list.append((jid, e))
                        seen.add((jid, e.index))
                continue
            except ValueError:
                pass
            j_lower = j.lower()
            for jid, e in selected:
                if j_lower in e.name.lower() and (jid, e.index) not in seen:
                    filtered_list.append((jid, e))
                    seen.add((jid, e.index))
        selected = filtered_list

    return selected


def _resolve_matrix_filters(
    options: RunOptions, cfg: LocalCIConfig
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    cli_matrix_include: list[dict[str, Any]] | None = None
    if options.matrix_filters:
        cli_matrix_include = [{}]
        for s in options.matrix_filters:
            if "=" in s:
                k, v = s.split("=", 1)
                cli_matrix_include[0][k.strip()] = v.strip()
        if not cli_matrix_include[0]:
            cli_matrix_include = None

    matrix_include = (
        cli_matrix_include
        if cli_matrix_include
        else (
            [f.model_dump(exclude_none=True) for f in cfg.matrix.include]
            if cfg.matrix.include
            else None
        )
    )
    matrix_exclude = (
        [f.model_dump(exclude_none=True) for f in cfg.matrix.exclude]
        if cfg.matrix.exclude
        else None
    )
    return matrix_include, matrix_exclude


def _run_preflight(executor: JobExecutor) -> int:
    try:
        act_version = executor.check_act()
        print_info(f"Using {act_version}")
    except ActNotFoundError as exc:
        print_error(str(exc))
        return 1

    try:
        executor.check_docker()
    except DockerNotAvailableError as exc:
        print_error(str(exc))
        return 1

    return 0


def _print_cache_enabled_message(cfg: LocalCIConfig, no_cache: bool) -> None:
    if no_cache or not cfg.cache.enabled:
        return
    cache_parts = []
    if cfg.cache.ccache.enabled:
        cache_parts.append("ccache")
    if cfg.cache.boost.enabled:
        cache_parts.append("boost")
    if cfg.cache.cmake.enabled:
        cache_parts.append("cmake")
    if getattr(cfg.cache.boost, "build_dir", True):
        cache_parts.append("b2-source")
    if getattr(cfg.cache, "apt", None) and getattr(cfg.cache.apt, "enabled", True):
        cache_parts.append("apt")
    if cache_parts:
        print_info(
            f"Cache enabled: {', '.join(cache_parts)}. Use --no-cache to disable."
        )


def _print_ccache_stats(
    container: RunDependencies, cfg: LocalCIConfig, options: RunOptions
) -> None:
    from localci.utils.output import console

    if options.no_cache or not cfg.cache.enabled or not cfg.cache.ccache.enabled:
        return
    resolved = container.resolve_cache_paths_fn(
        cfg.cache, options.no_cache, options.cache_dir, None, None
    )
    if resolved and resolved.ccache_host is not None:
        stats = container.get_ccache_stats_fn(resolved.ccache_host)
        if stats:
            print_info("ccache stats:")
            for line in stats.splitlines():
                console.print(f"  {line}")


def _save_execution_results(summary: ExecutionSummary, cfg: LocalCIConfig) -> None:
    logs_dir = Path(cfg.logging.directory)
    last_run_file = logs_dir / "last-run.json"
    execution_file = logs_dir / f"{summary.execution_id}.json"
    try:
        summary.save(last_run_file)
        summary.save(execution_file)
        print_info(f"Results saved to {last_run_file}")
        print_info(
            f"Execution ID: {summary.execution_id} (use with status -e or logs -e)"
        )
    except OSError as exc:
        print_warning(f"Could not save results: {exc}")
    except (TypeError, ValueError) as exc:
        print_warning(f"Could not serialize results for save: {exc}")
