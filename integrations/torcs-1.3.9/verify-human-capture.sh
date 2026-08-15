#!/usr/bin/env bash
# Compile and exercise the human telemetry CSV writer, then prove that the
# pinned TORCS 1.3.9 human-driver patch applies to the exact supplied archive.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCHIVE="${TORCS_ARCHIVE:-${REPO_ROOT}/torcs-1.3.9.tar.bz2}"
EXPECTED_SHA256="f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c"
WRITER_SOURCE="${SCRIPT_DIR}/overlay/src/drivers/human"
PATCH_FILE="${SCRIPT_DIR}/patches/human-telemetry.patch"

if [[ ! -f "${ARCHIVE}" ]]; then
    echo "TORCS archive not found: ${ARCHIVE}" >&2
    exit 2
fi
actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Unexpected TORCS archive SHA-256: ${actual_sha256}" >&2
    exit 2
fi

check_root="$(mktemp -d "${TMPDIR:-/tmp}/human-capture-verify.XXXXXX")"
trap 'rm -rf "${check_root}"' EXIT
mkdir -p "${check_root}/output"

g++ -std=gnu++98 -O2 -Wall -Wextra \
    -I"${WRITER_SOURCE}" \
    "${SCRIPT_DIR}/human_telemetry_writer_test.cpp" \
    "${WRITER_SOURCE}/apex_human_telemetry_writer.cpp" \
    -o "${check_root}/human-telemetry-writer-test"
"${check_root}/human-telemetry-writer-test" "${check_root}/output"

tar -xjf "${ARCHIVE}" -C "${check_root}" \
    torcs-1.3.9/src/drivers/human/human.cpp \
    torcs-1.3.9/src/drivers/human/Makefile \
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
    "${WRITER_SOURCE}/apex_human_telemetry.cpp" \
    -o "${check_root}/apex_human_telemetry.o"

human_source="${torcs_source}/src/drivers/human/human.cpp"
grep -q 'ApexHumanTelemetryStart' "${human_source}"
[[ "$(grep -c 'ApexHumanTelemetryRecord' "${human_source}")" -eq 2 ]]
grep -q 'ApexHumanTelemetryStop' "${human_source}"
grep -q 'apex_human_telemetry.cpp' "${check_root}/torcs-1.3.9/src/drivers/human/Makefile"

echo "Human telemetry writer and pinned-source patch checks passed"
