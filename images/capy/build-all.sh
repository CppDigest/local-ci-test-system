#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAVE=false
DATE_TAG="$(date +%F)"

usage() {
  cat <<EOF
Usage: $0 [--save]

Builds all capy Linux images in dependency order.

Options:
  --save   Export built images to .tar files under ${ROOT_DIR}/dist
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --save)
      SAVE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

build_image() {
  local image_name="$1"
  local dockerfile="$2"
  local context="${3:-${ROOT_DIR}}"

  echo "==> Building ${image_name}"
  docker build \
    -f "${dockerfile}" \
    -t "${image_name}:latest" \
    -t "${image_name}:${DATE_TAG}" \
    "${context}"
}

save_image() {
  local image_name="$1"
  mkdir -p "${ROOT_DIR}/dist"
  local out="${ROOT_DIR}/dist/${image_name}.tar"
  echo "==> Saving ${image_name}:latest to ${out}"
  docker save -o "${out}" "${image_name}:latest"
}

DOCKERFILES=(
  "capy-ubuntu-24.04-base:${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-base"
  "capy-ubuntu-25.04-base:${ROOT_DIR}/Dockerfile.capy-ubuntu-25.04-base"
  "capy-ubuntu-22.04-gcc12:${ROOT_DIR}/Dockerfile.capy-ubuntu-22.04-gcc12"
  "capy-ubuntu-24.04-gcc13-cov:${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-gcc13-cov"
  "capy-ubuntu-24.04-clang17:${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang17"
  "capy-ubuntu-24.04-clang20:${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang20"
  "capy-ubuntu-24.04-clang20-asan:${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang20-asan"
  "capy-ubuntu-24.04-clang20-x86:${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang20-x86"
  "capy-ubuntu-25.04-gcc15:${ROOT_DIR}/Dockerfile.capy-ubuntu-25.04-gcc15"
  "capy-ubuntu-25.04-gcc15-asan:${ROOT_DIR}/Dockerfile.capy-ubuntu-25.04-gcc15-asan"
)

for item in "${DOCKERFILES[@]}"; do
  image="${item%%:*}"
  dockerfile="${item#*:}"
  build_image "${image}" "${dockerfile}" "${ROOT_DIR}"
  if [[ "${SAVE}" == true ]]; then
    save_image "${image}"
  fi
done

echo
echo "Build complete."
if [[ "${SAVE}" == true ]]; then
  echo "Tar exports are under ${ROOT_DIR}/dist"
fi
