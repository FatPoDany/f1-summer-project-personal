#!/usr/bin/env bash
# Supply the development-only dependencies missing from the managed Rocky 8
# host without installing RPMs system-wide. PLIB is handled by its own script.

set -euo pipefail

BUILD_ROOT="${TORCS_BUILD_ROOT:-${TMPDIR:-/tmp}/apex-torcs-${UID}}"
DEPS_PREFIX="${TORCS_DEPS_PREFIX:-${BUILD_ROOT}/deps}"
RPM_DIR="${BUILD_ROOT}/rpms"
FREEALUT_SOURCE="${BUILD_ROOT}/freealut-1.1.0"
FREEALUT_BUILD="${BUILD_ROOT}/freealut-build-1.1.0"
FREEALUT_COMMIT="570dea5b77493cc9ce0b84f6a0a2ee31ed0b2c73"

required=(
    "${DEPS_PREFIX}/usr/include/AL/al.h"
    "${DEPS_PREFIX}/include/AL/alut.h"
    "${DEPS_PREFIX}/usr/include/ogg/ogg.h"
    "${DEPS_PREFIX}/usr/include/vorbis/vorbisfile.h"
    "${DEPS_PREFIX}/usr/include/X11/extensions/xf86vmode.h"
    "${DEPS_PREFIX}/lib/libalut.so"
)
ready=true
for path in "${required[@]}"; do
    [[ -e "${path}" ]] || ready=false
done
if [[ "${ready}" == true ]]; then
    echo "Rocky 8 TORCS development dependencies already present: ${DEPS_PREFIX}"
    exit 0
fi

for command in yumdownloader rpm rpm2cpio cpio cmake git ldconfig; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "Required command not found: ${command}" >&2
        exit 2
    fi
done

mkdir -p "${RPM_DIR}" "${DEPS_PREFIX}/lib"
packages=(openal-soft-devel libogg-devel libvorbis-devel libXxf86vm-devel)
for package in "${packages[@]}"; do
    yumdownloader --destdir="${RPM_DIR}" "${package}.x86_64"
done

shopt -s nullglob
rpms=("${RPM_DIR}"/*.rpm)
if [[ "${#rpms[@]}" -lt "${#packages[@]}" ]]; then
    echo "Not all required development RPMs were downloaded into ${RPM_DIR}" >&2
    exit 2
fi
for rpm_path in "${rpms[@]}"; do
    rpm --checksig "${rpm_path}"
    rpm2cpio "${rpm_path}" | cpio --quiet --directory "${DEPS_PREFIX}" -idm
done

link_runtime() {
    local linker_name="$1"
    local soname="$2"
    local runtime
    runtime="$(ldconfig -p | awk -v soname="${soname}" \
        '$1 == soname && $2 ~ /x86-64/ && runtime == "" { runtime = $NF } \
         END { print runtime }')"
    if [[ -z "${runtime}" || ! -e "${runtime}" ]]; then
        echo "Runtime library not found: ${soname}" >&2
        exit 2
    fi
    ln -sfn "${runtime}" "${DEPS_PREFIX}/lib/${linker_name}"
}

link_runtime libopenal.so libopenal.so.1
link_runtime libogg.so libogg.so.0
link_runtime libvorbis.so libvorbis.so.0
link_runtime libvorbisenc.so libvorbisenc.so.2
link_runtime libvorbisfile.so libvorbisfile.so.3
link_runtime libXxf86vm.so libXxf86vm.so.1

if [[ ! -d "${FREEALUT_SOURCE}/.git" ]]; then
    git clone --branch freealut_1_1_0 --depth 1 \
        https://github.com/vancegroup/freealut.git "${FREEALUT_SOURCE}"
fi
actual_commit="$(git -C "${FREEALUT_SOURCE}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${FREEALUT_COMMIT}" ]]; then
    echo "Refusing unexpected FreeALUT source commit: ${actual_commit}" >&2
    exit 2
fi
if [[ -n "$(git -C "${FREEALUT_SOURCE}" status --short)" ]]; then
    echo "Refusing a modified FreeALUT source tree: ${FREEALUT_SOURCE}" >&2
    exit 2
fi

openal_runtime="$(readlink -f "${DEPS_PREFIX}/lib/libopenal.so")"
cmake -S "${FREEALUT_SOURCE}" -B "${FREEALUT_BUILD}" \
    -DCMAKE_INSTALL_PREFIX="${DEPS_PREFIX}" \
    -DOPENAL_INCLUDE_DIR="${DEPS_PREFIX}/usr/include" \
    -DOPENAL_LIB="${openal_runtime}" \
    -DBUILD_TESTS=OFF \
    -DBUILD_STATIC=ON
cmake --build "${FREEALUT_BUILD}" --parallel 8 --target install

echo "Rocky 8 TORCS development dependencies ready: ${DEPS_PREFIX}"
