"""Individual workflow patch steps for the patch pipeline."""

from __future__ import annotations

import re

from localci.core.patch_pipeline import PatchContext, PatchStep


class ContainerMountsStep(PatchStep):
    """Inject cache bind-mount flags into the job container options."""

    @property
    def name(self) -> str:
        return "container_mounts"

    def apply(self, ctx: PatchContext) -> None:
        if not ctx.job_id or not ctx.container_mount_options:
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
                            existing = opt_match.group(2).strip().strip('"\'')
                            new_val = f"{existing} {ctx.container_mount_options}".strip()
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
                    break
            break


class B2SourceCacheStep(PatchStep):
    """Replace boost-root copy with persistent b2-source cache logic."""

    @property
    def name(self) -> str:
        return "b2_source_cache"

    def apply(self, ctx: PatchContext) -> None:
        for i, line in enumerate(ctx.lines):
            if "cp -rL boost-source boost-root" in line and "LOCALCI_B2_SOURCE_DIR" not in line:
                ind = line[: len(line) - len(line.lstrip())]
                ctx.lines[i] = (
                    f'{ind}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ] && [ -f "${{LOCALCI_B2_SOURCE_DIR}}/Jamroot" ]; then\n'
                    f'{ind}  # Cache hit: leave headers untouched (stable timestamps) so b2 builds incrementally\n'
                    f'{ind}  rm -rf "${{LOCALCI_B2_SOURCE_DIR}}/libs/capy" 2>/dev/null || true\n'
                    f'{ind}  ln -sfn "${{LOCALCI_B2_SOURCE_DIR}}" boost-root\n'
                    f'{ind}else\n'
                    f'{ind}  cp -rL boost-source boost-root\n'
                    f'{ind}  if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ]; then\n'
                    f'{ind}    mkdir -p "${{LOCALCI_B2_SOURCE_DIR}}"\n'
                    f'{ind}    cp -a boost-root/. "${{LOCALCI_B2_SOURCE_DIR}}/"\n'
                    f'{ind}  fi\n'
                    f'{ind}fi\n'
                )
                break


class RestoreCapyTimestampsStep(PatchStep):
    """Inject restore-timestamps step before Patch Boost."""

    @property
    def name(self) -> str:
        return "restore_capy_timestamps"

    def apply(self, ctx: PatchContext) -> None:
        for i, line in enumerate(ctx.lines):
            if re.match(r"^\s+-\s+name:\s+Patch Boost", line):
                already_patched = any(
                    "capy-file-stats" in ctx.lines[j]
                    for j in range(max(0, i - 15), i)
                )
                if not already_patched:
                    new_step = [
                        "      - name: Restore capy source file timestamps\n",
                        "        run: |\n",
                        '          if [ -n "${LOCALCI_B2_SOURCE_DIR:-}" ] && [ -f "${LOCALCI_B2_SOURCE_DIR}/.capy-file-stats" ]; then\n',
                        "            while IFS=' ' read -r saved_mtime fhash relpath; do\n",
                        '              [ -f "capy-root/$relpath" ] || continue\n',
                        '              curr=$(sha256sum "capy-root/$relpath" 2>/dev/null | cut -d\' \' -f1)\n',
                        '              [ "$curr" = "$fhash" ] && touch -d "@$saved_mtime" "capy-root/$relpath" 2>/dev/null || true\n',
                        '            done < "${LOCALCI_B2_SOURCE_DIR}/.capy-file-stats"\n',
                        '          fi\n',
                    ]
                    for j, new_line in enumerate(new_step):
                        ctx.lines.insert(i + j, new_line)
                break


class CapyCopyPreservationStep(PatchStep):
    """Use cp -rp and save capy file stats for incremental b2 builds."""

    @property
    def name(self) -> str:
        return "capy_copy_preservation"

    def apply(self, ctx: PatchContext) -> None:
        for i, line in enumerate(ctx.lines):
            if 'cp -r "$workspace_root"' in line and "libs/" in line:
                ind = line[: len(line) - len(line.lstrip())]
                ctx.lines[i] = (
                    f'{ind}cp -rp "$workspace_root"/capy-root "libs/$module"\n'
                    f'{ind}if [ -n "${{LOCALCI_B2_SOURCE_DIR:-}}" ]; then\n'
                    f'{ind}  find "$workspace_root/capy-root" -type f \\( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.ipp" \\) |\n'
                    f'{ind}  while IFS= read -r f; do\n'
                    f'{ind}    mtime=$(stat -c "%Y" "$f")\n'
                    f'{ind}    fhash=$(sha256sum "$f" | cut -d" " -f1)\n'
                    f'{ind}    echo "$mtime $fhash ${{f#$workspace_root/capy-root/}}"\n'
                    f'{ind}  done > "${{LOCALCI_B2_SOURCE_DIR}}/.capy-file-stats"\n'
                    f'{ind}fi\n'
                )
                break


class B2BootstrapSkipStep(PatchStep):
    """Stub bootstrap.sh when b2 binary is already cached."""

    @property
    def name(self) -> str:
        return "b2_bootstrap_skip"

    def apply(self, ctx: PatchContext) -> None:
        for i, line in enumerate(ctx.lines):
            if "b2-workflow" in line and "uses:" in line:
                step_start = i
                while step_start > 0:
                    if re.match(r"^\s+-\s+name:\s*", ctx.lines[step_start]):
                        break
                    step_start -= 1
                already_patched = any(
                    "LOCALCI_B2_SOURCE_DIR" in ctx.lines[j] and "bootstrap" in ctx.lines[j]
                    for j in range(max(0, step_start - 10), step_start)
                )
                if not already_patched:
                    new_step = [
                        "      - name: Skip b2 bootstrap (b2 binary cached)\n",
                        "        run: |\n",
                        '          if [ -n "${LOCALCI_B2_SOURCE_DIR:-}" ] && [ -f "${LOCALCI_B2_SOURCE_DIR}/b2" ]; then\n',
                        "            printf '#!/bin/sh\\necho \"b2 binary cached, skipping bootstrap.\"\\n' > boost-root/bootstrap.sh\n",
                        "            chmod +x boost-root/bootstrap.sh\n",
                        "          fi\n",
                    ]
                    for j, new_line in enumerate(new_step):
                        ctx.lines.insert(step_start + j, new_line)
                break


class ImageSubstitutionStep(PatchStep):
    """Replace matrix container image with the locally-built image tag."""

    @property
    def name(self) -> str:
        return "image_substitution"

    def apply(self, ctx: PatchContext) -> None:
        if not ctx.image_tag:
            return
        name_escaped = re.escape(ctx.entry.name)
        name_pattern = re.compile(r'name:\s*["\']?' + name_escaped + r'["\']?\s*$')
        name_idx = None
        for i, line in enumerate(ctx.lines):
            if name_pattern.search(line.strip()):
                name_idx = i
                break
        if name_idx is None:
            raise ValueError(f"Matrix entry name '{ctx.entry.name}' not found in workflow")

        name_line = ctx.lines[name_idx]
        name_indent = name_line[: len(name_line) - len(name_line.lstrip())]
        name_indent_len = len(name_indent)

        block_start = name_idx
        while block_start > 0:
            block_start -= 1
            line = ctx.lines[block_start]
            line_indent = line[: len(line) - len(line.lstrip())]
            if line.strip().startswith("-") and len(line_indent) <= name_indent_len:
                break

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

        container_pattern = re.compile(
            r"^(\s+)container:\s*[\"']?[^\"'\n]*[\"']?\s*$"
        )
        for i in range(block_start, block_end):
            mo = container_pattern.match(ctx.lines[i])
            if mo:
                ctx.lines[i] = f'{mo.group(1)}container: "{ctx.image_tag}"\n'
                break


class CodecovSkipStep(PatchStep):
    """Skip Codecov upload when running under act."""

    @property
    def name(self) -> str:
        return "codecov_skip"

    def apply(self, ctx: PatchContext) -> None:
        for i, line in enumerate(ctx.lines):
            if "https://codecov.io/bash" in line and "curl" in line:
                stripped = line.lstrip()
                if stripped.strip().startswith("bash <(curl") or "bash <(curl" in stripped:
                    indent = line[: len(line) - len(line.lstrip())]
                    rest = stripped.strip().rstrip()
                    act_check = 'if [ -z "${ACT:-}" ] || [ "$ACT" != "true" ]; then '
                    ctx.lines[i] = (
                        f"{indent}{act_check}{rest}; "
                        f'else echo "Skipping Codecov upload (running under act)."; fi\n'
                    )
                break


PATCH_STEP_REGISTRY: dict[str, type[PatchStep]] = {
    "container_mounts": ContainerMountsStep,
    "b2_source_cache": B2SourceCacheStep,
    "restore_capy_timestamps": RestoreCapyTimestampsStep,
    "capy_copy_preservation": CapyCopyPreservationStep,
    "b2_bootstrap_skip": B2BootstrapSkipStep,
    "image_substitution": ImageSubstitutionStep,
    "codecov_skip": CodecovSkipStep,
}
