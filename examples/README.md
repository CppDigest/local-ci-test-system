# Local CI installation validation example

This directory contains a **minimal sample project** that exercises the full Local CI path:

**`.localci.yml` → workflow YAML → image registry → act → results**

Use it after installing Local CI to confirm prerequisites, configuration, and Docker/act wiring before debugging a real repository.

| Path | Purpose |
|------|---------|
| [`validation-project/`](validation-project/) | Runnable example (workflow, config, registry) |
| [`validation-project/.github/workflows/validate.yml`](validation-project/.github/workflows/validate.yml) | Single-job workflow (one matrix row, one `echo` step) |
| [`validation-project/.localci.yml`](validation-project/.localci.yml) | Local CI settings tuned for smoke testing |
| [`validation-project/image-registry.yml`](validation-project/image-registry.yml) | Minimal registry entry (documents schema; no tarballs shipped) |

Related: [Issue #45](https://github.com/cppalliance/local-ci-test-system/issues/45) · Root [README](../README.md) (prerequisites, especially **mikefarah/yq v4+**) · [User Guide preflight checklist](../cli/Usage%20Guide.md#preflight-checklist-before-your-first-run)

---

## Prerequisites

Install these **before** running the example (same as the main project):

| Tool | Notes |
|------|--------|
| **Python 3.10+** | `pip install` from [`cli/`](../cli/) |
| **Docker** | Daemon running (`docker info` succeeds) |
| **[act](https://github.com/nektos/act)** | `act --version` (Windows: often `act-cli`) |
| **[mikefarah/yq](https://github.com/mikefarah/yq) v4+** | **Not** pip `yq` or [kislyuk/yq](https://github.com/kislyuk/yq). `yq --version` should mention mikefarah or v4.x. |

If the wrong `yq` is installed (or none), Local CI may fall back to PyYAML with a warning and limited expression support — see [README § yq](../README.md#yq-mikefarahyq-v4-or-newer).

Install Local CI:

```bash
cd /path/to/local-ci-test-system/cli
pip install .
localci --version
```

---

## Step-by-step validation (Linux)

All commands assume the repository root is `local-ci-test-system` and you use bash.

### 1. Preflight (host tools)

```bash
python --version    # 3.10+
docker version
docker info
act --version
yq --version        # mikefarah / v4+
localci --version
```

If any command fails, fix that tool before continuing — otherwise failures are easy to misread as “bad config.”

### 2. Enter the example project

```bash
cd examples/validation-project
```

### 3. Parse-only checks (no Docker/act execution)

```bash
localci analyze .github/workflows/validate.yml
localci list --platform linux
localci run --platform linux --dry-run
```

**Expected:** exit code `0`, a table showing one Linux job (`validate` / “Validation smoke”), no traceback.

### 4. Prepare the Docker image (one-time)

The workflow targets `ubuntu-latest` with GCC 15. Local CI derives the image tag **`capy-ubuntu-latest-gcc15:latest`**. The example registry points at that tag but does not ship image tarballs (`auto_build: false`).

Pull the standard act runner and tag it:

```bash
docker pull catthehacker/ubuntu:act-24.04
docker tag catthehacker/ubuntu:act-24.04 capy-ubuntu-latest-gcc15:latest
```

This matches how integration tests bootstrap images without building full capy layers.

### 5. Run the example

```bash
localci run --platform linux --no-cache
```

**Success looks like:**

- Exit code `0`
- Rich summary showing **1 passed** job
- `logs/last-run.json` created under `examples/validation-project/logs/`
- Job log contains `Local CI validation succeeded`

**Failure hints:**

| Symptom | Likely cause |
|---------|----------------|
| `act` not found | Install act; on Windows try `act-cli` |
| Docker connection error | Start Docker Desktop / daemon |
| Image pull / `No such image` | Repeat step 4 (tag step) |
| Workflow parse error | Wrong `yq` flavour — install mikefarah/yq v4+ |
| Job failed inside container | Run `localci logs "Validation smoke"` |

### 6. Inspect results

```bash
localci status
cat logs/last-run.json
localci logs "Validation smoke"
```

---

## macOS notes

- Install prerequisites via Homebrew where possible: `brew install yq`, Docker Desktop, act.
- Containers still run **Linux** images inside Docker Desktop — same `docker tag` step as Linux.
- If runs are flaky under heavy load, keep `parallel.max_jobs: 1` in `.localci.yml` (already set).

---

## Windows notes

- Use **Docker Desktop with the WSL2 backend** and run Local CI from WSL or a shell where `docker` and `localci` share the same environment.
- **yq:** `winget install MikeFarah.yq` or `choco install yq` — verify with `yq --version`.
- **act:** package may be named `act-cli`; preflight uses `act` or `act-cli` automatically.
- Prefer cloning the repo under the Linux filesystem inside WSL (`\\wsl$\...`) for bind-mount performance.
- Tag the image from PowerShell or WSL (same names):

  ```powershell
  docker pull catthehacker/ubuntu:act-24.04
  docker tag catthehacker/ubuntu:act-24.04 capy-ubuntu-latest-gcc15:latest
  ```

See [Cross-platform prerequisites](../cli/Usage%20Guide.md#cross-platform-prerequisites) for more detail.

---

## What this example deliberately does not do

- No Boost/ccache/cmake caches (disabled in `.localci.yml`)
- No automatic image builds (`images.auto_build: false`)
- No multi-job or multi-matrix coverage

After this passes, apply the same flow to your project: `localci config init`, point `workflow` at your CI file, and use the full [`image-registry.yml`](../image-registry.yml) when you need pre-built compiler images.
