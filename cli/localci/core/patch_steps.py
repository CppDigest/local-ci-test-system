"""Individual workflow patch steps for the patch pipeline."""

from __future__ import annotations

import re

from localci.core.config import PatchProjectConfig
from localci.core.patch_pipeline import PatchContext, PatchStep


def _leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _step_insert_indents(lines: list[str], step_start: int) -> tuple[str, str, str]:
    """Derive YAML indentation for inserting a new workflow step."""
    line = lines[step_start]
    step_match = re.match(r"^(\s+)-\s+name:\s*", line)
    if step_match:
        list_indent = step_match.group(1)
    else:
        dash_match = re.match(r"^(\s*)-\s+", line)
        if dash_match:
            list_indent = dash_match.group(1)
        else:
            list_indent = _leading_indent(line)
            if not line.strip():
                for idx in range(step_start - 1, max(-1, step_start - 6), -1):
                    if lines[idx].strip():
                        list_indent = _leading_indent(lines[idx])
                        break
    prop_indent = list_indent + "  "
    return list_indent, prop_indent, prop_indent + "  "


def _project_settings(ctx: PatchContext) -> PatchProjectConfig:
    return ctx.config.patches.project


class ContainerMountsStep(PatchStep):
    """Inject cache bind-mount flags into the job container options."""

    @property
    def name(self) -> str:
        return "container_mounts"

    def apply(self, ctx: PatchContext) -> None:
        if not ctx.job_id or not ctx.container_mount_options:
            self._skip("job_id and container_mount_options are required")
            return
        job_header = re.compile(r"^\s{2}" + re.escape(ctx.job_id) + r"\s*:\s*$")
        for i, line in enumerate(ctx.lines):
            if not job_header.match(line):
                continue
            for j in range(i + 1, len(ctx.lines)):
                row = ctx.lines[j]
                if row.strip() and (len(row) - len(row.lstrip())) <= 2:
                    break
                if re.match(r"^\s+container\s*:\s*$", row):
                    options_found = False
                    for k in range(j + 1, min(j + 10, len(ctx.lines))):
                        opt_match = re.match(r"^(\s+)options\s*:\s*(.*)$", ctx.lines[k])
                        if opt_match:
                            existing = opt_match.group(2).strip().strip("\"'")
                            new_val = (
                                f"{existing} {ctx.container_mount_options}".strip()
                            )
                            ctx.lines[k] = f'{opt_match.group(1)}options: "{new_val}"\n'
                            options_found = True
                            break
                    if not options_found:
                        container_indent = row[: len(row) - len(row.lstrip())]
                        options_indent = container_indent + "  "
                        ctx.lines.insert(
                            j + 1,
                            f'{options_indent}options: "{ctx.container_mount_options}"\n',
                        )
                    return
            self._skip(f"job {ctx.job_id!r} has no container block")
            return
        self._skip(f"job {ctx.job_id!r} not found in workflow")
        return


class B2SourceCacheStep(PatchStep):
    """Replace boost-root copy with persistent b2-source cache logic."""

    @property
    def name(self) -> str:
        return "b2_source_cache"

    def apply(self, ctx: PatchContext) -> None:
        proj = _project_settings(ctx)
        copy_cmd = proj.boost_source_copy_command
        boost_root = proj.boost_root_dir
        cached_libs = proj.cached_module_libs_path
        for i, line in enumerate(ctx.lines):
            if copy_cmd in line and "LOCALCI_B2_SOURCE_DIR" not in line:
                ind = line[: len(line) - len(line.lstrip())]
                ctx.lines[i] = (
                    f'{ind}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ] && [ -f "${{LOCALCI_B2_SOURCE_DIR}}/Jamroot" ]; then\n'
                    f"{ind}  # Cache hit: leave headers untouched (stable timestamps) so b2 builds incrementally\n"
                    f'{ind}  rm -rf "${{LOCALCI_B2_SOURCE_DIR}}/{cached_libs}" 2>/dev/null || true\n'
                    f'{ind}  ln -sfn "${{LOCALCI_B2_SOURCE_DIR}}" {boost_root}\n'
                    f"{ind}else\n"
                    f"{ind}  {copy_cmd}\n"
                    f'{ind}  if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ]; then\n'
                    f'{ind}    mkdir -p "${{LOCALCI_B2_SOURCE_DIR}}"\n'
                    f'{ind}    cp -a {boost_root}/. "${{LOCALCI_B2_SOURCE_DIR}}/"\n'
                    f"{ind}  fi\n"
                    f"{ind}fi\n"
                )
                return
        self._skip(f"no {copy_cmd!r} line found")
        return


class RestoreCapyTimestampsStep(PatchStep):
    """Inject restore-timestamps step before the dependency patch step."""

    @property
    def name(self) -> str:
        return "restore_capy_timestamps"

    def apply(self, ctx: PatchContext) -> None:
        proj = _project_settings(ctx)
        step_name = proj.patch_dependency_step_name
        source_dir = proj.project_source_dir
        stats_file = proj.file_stats_basename
        for i, line in enumerate(ctx.lines):
            step_match = re.match(
                rf"^(\s+)-\s+name:\s+{re.escape(step_name)}",
                line,
            )
            if not step_match:
                continue
            already_patched = any(
                stats_file in ctx.lines[j] for j in range(max(0, i - 15), i)
            )
            if already_patched:
                self._skip("restore step already present")
                return
            list_indent = step_match.group(1)
            prop_indent = list_indent + "  "
            body_indent = prop_indent + "  "
            new_step = [
                f"{list_indent}- name: {proj.restore_timestamps_step_title}\n",
                f"{prop_indent}run: |\n",
                f'{body_indent}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ] && [ -f "${{LOCALCI_B2_SOURCE_DIR}}/{stats_file}" ]; then\n',
                f"{body_indent}  while IFS=' ' read -r saved_mtime fhash relpath; do\n",
                f'{body_indent}    [ -f "{source_dir}/$relpath" ] || continue\n',
                f"{body_indent}    curr=$(sha256sum \"{source_dir}/$relpath\" 2>/dev/null | cut -d' ' -f1)\n",
                f'{body_indent}    [ "$curr" = "$fhash" ] && touch -d "@$saved_mtime" "{source_dir}/$relpath" 2>/dev/null || true\n',
                f'{body_indent}  done < "${{LOCALCI_B2_SOURCE_DIR}}/{stats_file}"\n',
                f"{body_indent}fi\n",
            ]
            for j, new_line in enumerate(new_step):
                ctx.lines.insert(i + j, new_line)
            return
        self._skip(f"{step_name!r} step not found")
        return


class CapyCopyPreservationStep(PatchStep):
    """Use cp -rp and save project file stats for incremental b2 builds."""

    @property
    def name(self) -> str:
        return "capy_copy_preservation"

    def apply(self, ctx: PatchContext) -> None:
        proj = _project_settings(ctx)
        marker = proj.workspace_libs_copy_marker
        dest = proj.workspace_libs_copy_dest
        source_dir = proj.project_source_dir
        stats_file = proj.file_stats_basename
        dest_prefix = dest.split("/")[0]
        for i, line in enumerate(ctx.lines):
            if marker in line and dest_prefix in line:
                ind = line[: len(line) - len(line.lstrip())]
                ctx.lines[i] = (
                    f'{ind}cp -rp "$workspace_root"/{source_dir} "{dest}"\n'
                    f'{ind}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ]; then\n'
                    f'{ind}  mkdir -p "${{LOCALCI_B2_SOURCE_DIR}}"\n'
                    f'{ind}  find "$workspace_root/{source_dir}" -type f \\( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.ipp" \\) |\n'
                    f"{ind}  while IFS= read -r f; do\n"
                    f'{ind}    mtime=$(stat -c "%Y" "$f")\n'
                    f'{ind}    fhash=$(sha256sum "$f" | cut -d" " -f1)\n'
                    f'{ind}    echo "$mtime $fhash ${{f#$workspace_root/{source_dir}/}}"\n'
                    f'{ind}  done > "${{LOCALCI_B2_SOURCE_DIR}}/{stats_file}"\n'
                    f"{ind}fi\n"
                )
                return
        self._skip(f"no {marker!r} workspace copy line found")
        return


class B2BootstrapSkipStep(PatchStep):
    """Stub bootstrap.sh when b2 binary is already cached."""

    @property
    def name(self) -> str:
        return "b2_bootstrap_skip"

    def apply(self, ctx: PatchContext) -> None:
        proj = _project_settings(ctx)
        action_marker = proj.b2_workflow_action_marker
        boost_root = proj.boost_root_dir
        for i, line in enumerate(ctx.lines):
            if action_marker in line and "uses:" in line:
                step_start = i
                while step_start > 0:
                    if re.match(r"^\s+-\s+name:\s*", ctx.lines[step_start]):
                        break
                    step_start -= 1
                already_patched = any(
                    "Skip b2 bootstrap" in ctx.lines[j]
                    for j in range(max(0, step_start - 15), step_start)
                )
                if already_patched:
                    self._skip("skip b2 bootstrap step already present")
                    return
                list_indent, prop_indent, body_indent = _step_insert_indents(
                    ctx.lines, step_start
                )
                new_step = [
                    f"{list_indent}- name: Skip b2 bootstrap (b2 binary cached)\n",
                    f"{prop_indent}run: |\n",
                    f'{body_indent}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ] && [ -f "${{LOCALCI_B2_SOURCE_DIR}}/b2" ]; then\n',
                    f"{body_indent}  printf '#!/bin/sh\\necho \"b2 binary cached, skipping bootstrap.\"\\n' > {boost_root}/bootstrap.sh\n",
                    f"{body_indent}  chmod +x {boost_root}/bootstrap.sh\n",
                    f"{body_indent}fi\n",
                ]
                for j, new_line in enumerate(new_step):
                    ctx.lines.insert(step_start + j, new_line)
                return
        self._skip(f"no {action_marker!r} uses step found")
        return


class ImageSubstitutionStep(PatchStep):
    """Replace matrix container image with the locally-built image tag.

    Raises ValueError if the matrix entry name is not found (unexpected workflow
    structure). Skips with a warning if the container field is absent from the
    entry block (valid but unsupported layout).
    """

    @property
    def name(self) -> str:
        return "image_substitution"

    def apply(self, ctx: PatchContext) -> None:
        if not ctx.image_tag:
            self._skip("image_tag not provided")
            return
        name_escaped = re.escape(ctx.entry.name)
        name_pattern = re.compile(r'name:\s*["\']?' + name_escaped + r'["\']?\s*$')
        name_idx = None
        for i, line in enumerate(ctx.lines):
            if name_pattern.search(line.strip()):
                name_idx = i
                break
        if name_idx is None:
            raise ValueError(
                f"Matrix entry name '{ctx.entry.name}' not found in workflow"
            )

        name_line = ctx.lines[name_idx]
        name_indent = name_line[: len(name_line) - len(name_line.lstrip())]
        name_indent_len = len(name_indent)

        block_start = name_idx
        while True:
            line = ctx.lines[block_start]
            line_indent = _leading_indent(line)
            if line.strip().startswith("-") and len(line_indent) <= name_indent_len:
                break
            if block_start == 0:
                break
            block_start -= 1

        list_item_indent = ctx.lines[block_start][
            : len(ctx.lines[block_start]) - len(ctx.lines[block_start].lstrip())
        ]
        list_item_indent_len = len(list_item_indent)
        block_end = name_idx + 1
        while block_end < len(ctx.lines):
            line = ctx.lines[block_end]
            line_indent = line[: len(line) - len(line.lstrip())]
            if line_indent == list_item_indent and line.strip().startswith("-"):
                break
            if len(line_indent) < list_item_indent_len:
                break
            block_end += 1

        container_pattern = re.compile(r"^(\s+)container:\s*[\"']?[^\"'\n]*[\"']?\s*$")
        for i in range(block_start, block_end):
            mo = container_pattern.match(ctx.lines[i])
            if mo:
                ctx.lines[i] = f'{mo.group(1)}container: "{ctx.image_tag}"\n'
                return
        self._skip("container field not found in matrix entry block")
        return


class CodecovSkipStep(PatchStep):
    """Skip Codecov upload when running under act."""

    @property
    def name(self) -> str:
        return "codecov_skip"

    def apply(self, ctx: PatchContext) -> None:
        for i, line in enumerate(ctx.lines):
            if "https://codecov.io/bash" in line and "curl" in line:
                stripped = line.lstrip()
                if (
                    stripped.strip().startswith("bash <(curl")
                    or "bash <(curl" in stripped
                ):
                    indent = line[: len(line) - len(line.lstrip())]
                    rest = stripped.strip().rstrip()
                    act_check = 'if [ -z "${ACT:-}" ] || [ "$ACT" != "true" ]; then '
                    ctx.lines[i] = (
                        f"{indent}{act_check}{rest}; "
                        f'else echo "Skipping Codecov upload (running under act)."; fi\n'
                    )
                    return
        self._skip("no Codecov upload step found")
        return


PATCH_STEP_REGISTRY: dict[str, type[PatchStep]] = {
    "container_mounts": ContainerMountsStep,
    "b2_source_cache": B2SourceCacheStep,
    "restore_capy_timestamps": RestoreCapyTimestampsStep,
    "capy_copy_preservation": CapyCopyPreservationStep,
    "b2_bootstrap_skip": B2BootstrapSkipStep,
    "image_substitution": ImageSubstitutionStep,
    "codecov_skip": CodecovSkipStep,
}
