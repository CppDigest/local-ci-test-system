#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATE_TAG="$(date +%F)"
SAVE=false

declare -A FILES=(
  ["capy-ubuntu-24.04-base"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-base"
  ["capy-ubuntu-25.04-base"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-25.04-base"
  ["capy-ubuntu-22.04-gcc12"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-22.04-gcc12"
  ["capy-ubuntu-24.04-gcc13-cov"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-gcc13-cov"
  ["capy-ubuntu-24.04-clang17"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang17"
  ["capy-ubuntu-24.04-clang20"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang20"
  ["capy-ubuntu-24.04-clang20-asan"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang20-asan"
  ["capy-ubuntu-24.04-clang20-x86"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-24.04-clang20-x86"
  ["capy-ubuntu-25.04-gcc15"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-25.04-gcc15"
  ["capy-ubuntu-25.04-gcc15-asan"]="${ROOT_DIR}/Dockerfile.capy-ubuntu-25.04-gcc15-asan"
)

usage() {
  cat <<EOF
Usage: $0 <image-name> [--save]

Image names:
  ${!FILES[*]}

Options:
  --save   Export image as dist/<image-name>.tar
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

# Handle --help before assigning IMAGE_NAME so "build-one.sh --help" works
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

IMAGE_NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --save)
      SAVE=true
      shift
      ;;
    *)
      if [[ -z "${IMAGE_NAME:-}" ]]; then
        IMAGE_NAME="$1"
      else
        echo "Unknown argument: $1" >&2
        usage
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "${IMAGE_NAME:-}" ]]; then
  usage
  exit 1
fi

DOCKERFILE="${FILES[$IMAGE_NAME]:-}"
if [[ -z "${DOCKERFILE}" ]]; then
  echo "Unsupported image: ${IMAGE_NAME}" >&2
  usage
  exit 1
fi

echo "==> Building ${IMAGE_NAME}"
docker build \
  -f "${DOCKERFILE}" \
  -t "${IMAGE_NAME}:latest" \
  -t "${IMAGE_NAME}:${DATE_TAG}" \
  "${ROOT_DIR}"

if [[ "${SAVE}" == true ]]; then
  mkdir -p "${ROOT_DIR}/dist"
  OUT="${ROOT_DIR}/dist/${IMAGE_NAME}.tar"
  echo "==> Saving ${IMAGE_NAME}:latest to ${OUT}"
  docker save -o "${OUT}" "${IMAGE_NAME}:latest"
fi

echo "Done."
