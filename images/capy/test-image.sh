#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 <image-name>

Validates required tools and variant-specific requirements.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

IMAGE="$1"

run_check() {
  local description="$1"
  local command="$2"
  echo " - ${description}"
  docker run --rm --entrypoint bash "${IMAGE}" -lc "${command}" >/dev/null
}

echo "Validating image: ${IMAGE}"

# Common checks
run_check "git present" "command -v git"
run_check "cmake present" "command -v cmake"
run_check "ninja present" "command -v ninja"
run_check "ccache present" "command -v ccache"
run_check "b2 present" "command -v b2"
run_check "boost source pre-cloned" "test -d /opt/boost-source"
run_check "node present (for act)" "command -v node"
run_check "openssl dev headers present" "test -f /usr/include/openssl/ssl.h"
run_check "zlib dev headers present" "test -f /usr/include/zlib.h"

case "${IMAGE}" in
  *gcc15*)
    run_check "gcc-15 present" "command -v gcc-15"
    run_check "g++-15 present" "command -v g++-15"
    ;;
  *gcc13*)
    run_check "gcc-13 present" "command -v gcc-13"
    run_check "g++-13 present" "command -v g++-13"
    ;;
  *gcc12*)
    run_check "gcc-12 present" "command -v gcc-12"
    run_check "g++-12 present" "command -v g++-12"
    ;;
esac

case "${IMAGE}" in
  *clang17*)
    run_check "clang-17 present" "command -v clang-17"
    run_check "clang++-17 present" "command -v clang++-17"
    ;;
  *clang20*)
    run_check "clang-20 present" "command -v clang-20"
    run_check "clang++-20 present" "command -v clang++-20"
    ;;
esac

case "${IMAGE}" in
  *asan*)
    run_check "ASAN runtime package present" "ldconfig -p | grep -E 'libasan|libubsan'"
    ;;
  *cov*)
    run_check "lcov present" "command -v lcov"
    ;;
  *x86*)
    run_check "x86 toolchain present" "dpkg -s gcc-multilib >/dev/null"
    run_check "i386 libs present" "dpkg -s libc6-dev-i386 >/dev/null"
    ;;
esac

echo "Validation passed: ${IMAGE}"
