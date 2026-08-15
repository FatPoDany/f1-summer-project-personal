#!/usr/bin/env bash
# Prove that the pinned TORCS source receives a distinct graphical study-race
# entry and that the shipped development preset is human-only and versioned.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCHIVE="${TORCS_ARCHIVE:-${REPO_ROOT}/torcs-1.3.9.tar.bz2}"
EXPECTED_SHA256="f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c"
PATCH_FILE="${SCRIPT_DIR}/patches/graphical-race.patch"
SCREEN_PATCH_FILE="${SCRIPT_DIR}/patches/screen-size-init.patch"
PRESET_FILE="${SCRIPT_DIR}/overlay/src/raceman/apexstudy.xml"

[[ -f "${ARCHIVE}" ]] || { echo "TORCS archive not found: ${ARCHIVE}" >&2; exit 2; }
[[ -f "${PATCH_FILE}" ]] || { echo "Graphical race patch not found: ${PATCH_FILE}" >&2; exit 1; }
[[ -f "${SCREEN_PATCH_FILE}" ]] || { echo "Screen size patch not found: ${SCREEN_PATCH_FILE}" >&2; exit 1; }
[[ -f "${PRESET_FILE}" ]] || { echo "Study preset not found: ${PRESET_FILE}" >&2; exit 1; }

actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Unexpected TORCS archive SHA-256: ${actual_sha256}" >&2
    exit 2
fi

check_root="$(mktemp -d "${TMPDIR:-/tmp}/study-preset-verify.XXXXXX")"
trap 'rm -rf "${check_root}"' EXIT
tar -xjf "${ARCHIVE}" -C "${check_root}" \
    torcs-1.3.9/src/linux/main.cpp \
    torcs-1.3.9/src/linux/torcs.in \
    torcs-1.3.9/src/libs/tgfclient/screen.cpp \
    torcs-1.3.9/src/libs/raceengineclient/raceinit.cpp \
    torcs-1.3.9/src/libs/raceengineclient/raceinit.h

patch --batch --forward --directory="${check_root}/torcs-1.3.9" \
    --strip=1 --input="${PATCH_FILE}"
patch --batch --forward --directory="${check_root}/torcs-1.3.9" \
    --strip=1 --input="${SCREEN_PATCH_FILE}"

source_root="${check_root}/torcs-1.3.9/src"
main_source="${source_root}/linux/main.cpp"
race_source="${source_root}/libs/raceengineclient/raceinit.cpp"
race_header="${source_root}/libs/raceengineclient/raceinit.h"
screen_source="${source_root}/libs/tgfclient/screen.cpp"
launcher="${source_root}/linux/torcs.in"

grep -q 'strcmp(argv\[i\], "-R")' "${main_source}"
graphical_line="$(grep -n 'strcmp(argv\[i\], "-R")' "${main_source}" | cut -d: -f1)"
freeglut_line="$(grep -n '#ifndef FREEGLUT' "${main_source}" | head -n 1 | cut -d: -f1)"
[[ "${graphical_line}" -lt "${freeglut_line}" ]]
grep -q 'ReRunRaceOnGUI(graphicalraceconfig)' "${main_source}"
grep -q 'ReRunRaceOnConsole(raceconfig)' "${main_source}"
grep -q 'GfScrInit(argc, argv)' "${main_source}"
grep -q 'glutMainLoop()' "${main_source}"
grep -q 'void ReRunRaceOnGUI(const char\* raceconfig)' "${race_source}"
grep -q 'ReStateApply((void \*) RE_STATE_EVENT_INIT)' "${race_source}"
grep -q 'extern void ReRunRaceOnGUI(const char\* raceconfig)' "${race_header}"
grep -q 'GfScrWidth = xw;' "${screen_source}"
grep -q 'GfScrHeight = yw;' "${screen_source}"
grep -q 'APEX_TORCS_LOCAL_DIR' "${launcher}"

grep -q '<params name="Apex Study v1"' "${PRESET_FILE}"
grep -q '<attstr name="name" val="g-track-1"/>' "${PRESET_FILE}"
grep -q '<attstr name="category" val="road"/>' "${PRESET_FILE}"
grep -q '<attnum name="laps" val="5"/>' "${PRESET_FILE}"
grep -q '<attstr name="module" val="human"/>' "${PRESET_FILE}"
[[ "$(grep -c '<attstr name="module"' "${PRESET_FILE}")" -eq 1 ]]

echo "Graphical study preset source contract passed"
