#!/usr/bin/env bash
# Compile the robot telemetry writer/adapter and prove the pinned berniw patch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCHIVE="${TORCS_ARCHIVE:-${REPO_ROOT}/torcs-1.3.9.tar.bz2}"
EXPECTED_SHA256="f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c"
WRITER_SOURCE="${SCRIPT_DIR}/overlay/src/drivers/berniw"
PATCH_FILE="${SCRIPT_DIR}/patches/berniw-telemetry.patch"

grep -q 'patches/berniw-telemetry.patch' "${SCRIPT_DIR}/build.sh"
grep -q 'ApexRobotTelemetryRecord' "${SCRIPT_DIR}/build.sh"

if [[ ! -f "${ARCHIVE}" ]]; then
    echo "TORCS archive not found: ${ARCHIVE}" >&2
    exit 2
fi
actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Unexpected TORCS archive SHA-256: ${actual_sha256}" >&2
    exit 2
fi
if [[ ! -f "${PATCH_FILE}" ]]; then
    echo "Reference recorder patch not found: ${PATCH_FILE}" >&2
    exit 2
fi

check_root="$(mktemp -d "${TMPDIR:-/tmp}/robot-capture-verify.XXXXXX")"
trap 'rm -rf "${check_root}"' EXIT
mkdir -p "${check_root}/output"

g++ -std=gnu++98 -O2 -Wall -Wextra \
    -I"${WRITER_SOURCE}" \
    "${SCRIPT_DIR}/robot_telemetry_writer_test.cpp" \
    "${WRITER_SOURCE}/apex_robot_telemetry_writer.cpp" \
    -o "${check_root}/robot-telemetry-writer-test"
"${check_root}/robot-telemetry-writer-test" "${check_root}/output"

tar -xjf "${ARCHIVE}" -C "${check_root}" \
    torcs-1.3.9/src/drivers/berniw/berniw.cpp \
    torcs-1.3.9/src/drivers/berniw/Makefile \
    torcs-1.3.9/src/interfaces \
    torcs-1.3.9/src/linux/osspec.h \
    torcs-1.3.9/src/libs/math \
    torcs-1.3.9/src/libs/robottools/robottools.h \
    torcs-1.3.9/src/libs/tgf/tgf.h \
    torcs-1.3.9/src/windows/include/plib
patch --batch --forward --directory="${check_root}/torcs-1.3.9" \
    --strip=1 --input="${PATCH_FILE}"

torcs_source="${check_root}/torcs-1.3.9"
ln -s math "${torcs_source}/src/libs/tmath"
g++ -std=gnu++98 -O2 -Wall -Wextra -fPIC -c \
    -I"${WRITER_SOURCE}" \
    -I"${torcs_source}/src/windows/include" \
    -I"${torcs_source}/src/linux" \
    -I"${torcs_source}/src/interfaces" \
    -I"${torcs_source}/src/libs" \
    -I"${torcs_source}/src/libs/tgf" \
    -I"${torcs_source}/src/libs/robottools" \
    "${WRITER_SOURCE}/apex_robot_telemetry.cpp" \
    -o "${check_root}/apex_robot_telemetry.o"

berniw_source="${torcs_source}/src/drivers/berniw/berniw.cpp"
grep -q 'ApexRobotTelemetryStart' "${berniw_source}"
[[ "$(grep -c 'ApexRobotTelemetryRecord' "${berniw_source}")" -eq 1 ]]
grep -q 'ApexRobotTelemetryStop' "${berniw_source}"
grep -q 'apex_robot_telemetry.cpp' "${torcs_source}/src/drivers/berniw/Makefile"
grep -q 'apex_robot_telemetry_writer.cpp' "${torcs_source}/src/drivers/berniw/Makefile"

if sed -n '/^+[^+]/p' "${PATCH_FILE}" | \
    grep -Eq '(_accelCmd|_brakeCmd|_steerCmd|_clutchCmd|_gearCmd)[[:space:]]*='; then
    echo "Reference recorder patch must not assign actuators" >&2
    exit 1
fi

echo "Reference robot telemetry writer and pinned-source checks passed"
