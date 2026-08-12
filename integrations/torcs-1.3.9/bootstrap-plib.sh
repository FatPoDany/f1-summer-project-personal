#!/usr/bin/env bash
# Build TORCS' legacy PLIB dependency into the same user-local /tmp tree.

set -euo pipefail

BUILD_ROOT="${TORCS_BUILD_ROOT:-${TMPDIR:-/tmp}/apex-torcs-${UID}}"
PREFIX="${TORCS_DEPS_PREFIX:-${BUILD_ROOT}/deps}"
DIST_DIR="${BUILD_ROOT}/distfiles"
SOURCE_ROOT="${BUILD_ROOT}/deps-src"
ARCHIVE="${DIST_DIR}/plib-1.8.5.tar.gz"
SOURCE_DIR="${SOURCE_ROOT}/plib-1.8.5"
URL="https://plib.sourceforge.net/dist/plib-1.8.5.tar.gz"
EXPECTED_SHA256="485b22bf6fdc0da067e34ead5e26f002b76326f6371e2ae006415dea6a380a32"

mkdir -p "${DIST_DIR}" "${SOURCE_ROOT}"
if [[ ! -f "${ARCHIVE}" ]]; then
    curl --fail --location --show-error --retry 5 --retry-delay 2 \
        --output "${ARCHIVE}.part" "${URL}"
    mv "${ARCHIVE}.part" "${ARCHIVE}"
fi
actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Refusing unexpected PLIB archive SHA-256: ${actual_sha256}" >&2
    exit 2
fi

if [[ ! -f "${SOURCE_DIR}/configure" ]]; then
    tar -xzf "${ARCHIVE}" -C "${SOURCE_ROOT}"
fi
if [[ ! -f "${SOURCE_DIR}/config.status" ]]; then
    (
        cd "${SOURCE_DIR}"
        env CFLAGS="${CFLAGS:--O2 -fPIC}" \
            CXXFLAGS="${CXXFLAGS:--O2 -fPIC -fpermissive}" \
            ./configure --prefix="${PREFIX}"
    )
fi

jobs="${TORCS_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
make --silent -C "${SOURCE_DIR}" -j"${jobs}"
make --silent -C "${SOURCE_DIR}" install
echo "PLIB 1.8.5 ready: ${PREFIX}"
