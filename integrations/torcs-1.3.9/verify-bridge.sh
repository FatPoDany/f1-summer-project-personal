#!/usr/bin/env bash
# Compile the Granite bridge against the exact TORCS 1.3.9 headers without
# requiring a complete TORCS/PLIB installation. This is a build-contract test,
# not a substitute for installing and running the simulator.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCHIVE="${TORCS_ARCHIVE:-${REPO_ROOT}/torcs-1.3.9.tar.bz2}"
EXPECTED_SHA256="f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c"
OUTPUT_DIR="${TORCS_BRIDGE_OUTPUT:-${REPO_ROOT}/build/bridge-check}"

if [[ ! -f "${ARCHIVE}" ]]; then
    echo "TORCS archive not found: ${ARCHIVE}" >&2
    exit 2
fi
actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Unexpected TORCS archive SHA-256: ${actual_sha256}" >&2
    exit 2
fi

check_root="$(mktemp -d "${TMPDIR:-/tmp}/granite-bridge-verify.XXXXXX")"
trap 'rm -rf "${check_root}"' EXIT

tar -xjf "${ARCHIVE}" -C "${check_root}" \
    torcs-1.3.9/src/interfaces \
    torcs-1.3.9/src/linux/osspec.h \
    torcs-1.3.9/src/libs/math \
    torcs-1.3.9/src/libs/robottools/robottools.h \
    torcs-1.3.9/src/libs/tgf/tgf.h \
    torcs-1.3.9/src/windows/include/plib

torcs_source="${check_root}/torcs-1.3.9"
ln -s math "${torcs_source}/src/libs/tmath"
bridge_source="${SCRIPT_DIR}/overlay/src/drivers/granite_bridge"
mkdir -p "${OUTPUT_DIR}"

g++ -std=gnu++98 -O2 -Wall -Wextra \
    -I"${bridge_source}" \
    "${SCRIPT_DIR}/coach_protocol_test.cpp" \
    -o "${check_root}/coach-protocol-test"
"${check_root}/coach-protocol-test"

g++ -std=gnu++98 -O2 -Wall -Wextra -fPIC -shared \
    -I"${torcs_source}/src/windows/include" \
    -I"${torcs_source}/src/linux" \
    -I"${torcs_source}/src/interfaces" \
    -I"${torcs_source}/src/libs" \
    -I"${torcs_source}/src/libs/tgf" \
    -I"${torcs_source}/src/libs/robottools" \
    "${bridge_source}/granite_bridge.cpp" \
    "${bridge_source}/sensors.cpp" \
    "${bridge_source}/ObstacleSensors.cpp" \
    -o "${OUTPUT_DIR}/granite_bridge.so"

if ! nm -D "${OUTPUT_DIR}/granite_bridge.so" | grep -q ' T granite_bridge$'; then
    echo "Compiled module does not export the TORCS granite_bridge entry point" >&2
    exit 1
fi

echo "Bridge protocol and ABI checks passed: ${OUTPUT_DIR}/granite_bridge.so"
