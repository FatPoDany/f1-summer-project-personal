#!/usr/bin/env bash
# Prepare and build the user's verified TORCS 1.3.9 archive with the
# loopback-only SCR robot used by racecoach. Generated source/runtime trees
# stay under the ignored build/ directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCHIVE="${TORCS_ARCHIVE:-${REPO_ROOT}/torcs-1.3.9.tar.bz2}"
BUILD_ROOT="${TORCS_BUILD_ROOT:-${TMPDIR:-/tmp}/apex-torcs-${UID}}"
SOURCE_DIR="${BUILD_ROOT}/torcs-1.3.9"
PREFIX="${TORCS_PREFIX:-${BUILD_ROOT}/torcs-runtime}"
DEPS_PREFIX="${TORCS_DEPS_PREFIX:-${BUILD_ROOT}/deps}"
EXPECTED_SHA256="f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c"
ACTION="${1:-install}"

case "${ACTION}" in
    prepare|build|install) ;;
    *)
        echo "Usage: $0 [prepare|build|install]" >&2
        exit 2
        ;;
esac

if [[ ! -f "${ARCHIVE}" ]]; then
    echo "TORCS archive not found: ${ARCHIVE}" >&2
    echo "Set TORCS_ARCHIVE or put torcs-1.3.9.tar.bz2 in the repository root." >&2
    exit 2
fi

actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Refusing unexpected TORCS archive." >&2
    echo "Expected: ${EXPECTED_SHA256}" >&2
    echo "Actual:   ${actual_sha256}" >&2
    exit 2
fi

mkdir -p "${BUILD_ROOT}"
extract_marker="${SOURCE_DIR}/.apex-archive-complete"
if [[ ! -f "${extract_marker}" ]]; then
    echo "Extracting verified TORCS 1.3.9 archive into ${BUILD_ROOT}"
    tar -xjf "${ARCHIVE}" -C "${BUILD_ROOT}"
    touch "${extract_marker}"
fi

echo "Applying the stock-1.3.9 SCR compatibility overlay"
cp -a "${SCRIPT_DIR}/overlay/." "${SOURCE_DIR}/"

human_source="${SOURCE_DIR}/src/drivers/human/human.cpp"
human_makefile="${SOURCE_DIR}/src/drivers/human/Makefile"
human_patch="${SCRIPT_DIR}/patches/human-telemetry.patch"
graphical_patch="${SCRIPT_DIR}/patches/graphical-race.patch"
screen_patch="${SCRIPT_DIR}/patches/screen-size-init.patch"
screen_source="${SOURCE_DIR}/src/libs/tgfclient/screen.cpp"
human_record_calls="$(grep -c 'ApexHumanTelemetryRecord' "${human_source}" || true)"
if ! grep -q 'ApexHumanTelemetryStart' "${human_source}" || \
   [[ "${human_record_calls}" -ne 2 ]] || \
   ! grep -q 'ApexHumanTelemetryStop' "${human_source}" || \
   ! grep -q 'apex_human_telemetry.cpp' "${human_makefile}" || \
   ! grep -q 'apex_human_telemetry_writer.cpp' "${human_makefile}"; then
    echo "Applying the opt-in human telemetry capture patch"
   patch --batch --forward --directory="${SOURCE_DIR}" --strip=1 --input="${human_patch}"
fi

if ! grep -q 'ReRunRaceOnGUI(graphicalraceconfig)' "${SOURCE_DIR}/src/linux/main.cpp" || \
   ! grep -q 'APEX_TORCS_LOCAL_DIR' "${SOURCE_DIR}/src/linux/torcs.in"; then
    echo "Applying the graphical study-race patch"
    patch --batch --forward --directory="${SOURCE_DIR}" --strip=1 --input="${graphical_patch}"
fi

if ! grep -q 'GfScrWidth = xw;' "${screen_source}" || \
   ! grep -q 'GfScrHeight = yw;' "${screen_source}"; then
    echo "Applying the initial screen-size patch"
    patch --batch --forward --directory="${SOURCE_DIR}" --strip=1 --input="${screen_patch}"
fi

if [[ "${ACTION}" == "prepare" ]]; then
    echo "Prepared source: ${SOURCE_DIR}"
    exit 0
fi

echo "Configuring a user-local TORCS runtime at ${PREFIX}"
torcs_cppflags="${CPPFLAGS:-}"
torcs_ldflags="${LDFLAGS:-}"
if [[ -f "${DEPS_PREFIX}/include/plib/ssg.h" ]]; then
    torcs_cppflags="${torcs_cppflags} -I${DEPS_PREFIX}/include"
    torcs_ldflags="${torcs_ldflags} -L${DEPS_PREFIX}/lib -Wl,-rpath,${DEPS_PREFIX}/lib"
fi
if [[ -d "${DEPS_PREFIX}/usr/include" ]]; then
    torcs_cppflags="${torcs_cppflags} -I${DEPS_PREFIX}/usr/include"
fi
(
    cd "${SOURCE_DIR}"
    env \
        CPPFLAGS="${torcs_cppflags}" \
        LDFLAGS="${torcs_ldflags}" \
        CXXFLAGS="${CXXFLAGS:--O2 -fPIC -fpermissive}" \
        ./configure --prefix="${PREFIX}" --disable-xrandr
)

# TORCS 1.3.9's export phase is not parallel-safe: dependent directories can
# compile before generated headers exist. Keep the reproducible default serial.
jobs="${TORCS_JOBS:-1}"
echo "Building TORCS with ${jobs} job(s)"
make -C "${SOURCE_DIR}" -j"${jobs}"

if [[ "${ACTION}" == "build" ]]; then
    echo "Built source tree: ${SOURCE_DIR}"
    exit 0
fi

echo "Installing TORCS and its data into ${PREFIX}"
make -C "${SOURCE_DIR}" install
make -C "${SOURCE_DIR}" datainstall

echo "TORCS runtime ready: ${PREFIX}/bin/torcs"
