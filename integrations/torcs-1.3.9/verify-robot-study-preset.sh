#!/usr/bin/env bash
# Verify the fixed unattended three-lap reference-robot race assignment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCHIVE="${TORCS_ARCHIVE:-${REPO_ROOT}/torcs-1.3.9.tar.bz2}"
EXPECTED_SHA256="f9c69e86d290295467451b01d7838d85005ba613644a6fe8a3f85a7c6a03cd4c"
PRESET_FILE="${SCRIPT_DIR}/overlay/src/raceman/apexrobotstudy.xml"

[[ -f "${ARCHIVE}" ]] || { echo "TORCS archive not found: ${ARCHIVE}" >&2; exit 2; }
[[ -f "${PRESET_FILE}" ]] || { echo "Robot study preset not found: ${PRESET_FILE}" >&2; exit 1; }

actual_sha256="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${EXPECTED_SHA256}" ]]; then
    echo "Unexpected TORCS archive SHA-256: ${actual_sha256}" >&2
    exit 2
fi

grep -q '<params name="Apex Robot Study v1"' "${PRESET_FILE}"
grep -q '<attstr name="name" val="g-track-1"/>' "${PRESET_FILE}"
grep -q '<attstr name="category" val="road"/>' "${PRESET_FILE}"
grep -q '<attnum name="laps" val="3"/>' "${PRESET_FILE}"
grep -q '<attnum name="idx" val="9"/>' "${PRESET_FILE}"
grep -q '<attstr name="module" val="berniw"/>' "${PRESET_FILE}"
[[ "$(grep -c '<attstr name="module"' "${PRESET_FILE}")" -eq 1 ]]
grep -q '<attstr name="restart" val="no"/>' "${PRESET_FILE}"
grep -q '<attnum name="fuel consumption factor" val="1"/>' "${PRESET_FILE}"
grep -q '<attnum name="damage factor" val="1"/>' "${PRESET_FILE}"
grep -q '<attnum name="tire factor" val="1"/>' "${PRESET_FILE}"

check_root="$(mktemp -d "${TMPDIR:-/tmp}/robot-preset-verify.XXXXXX")"
trap 'rm -rf "${check_root}"' EXIT
tar -xjf "${ARCHIVE}" -C "${check_root}" torcs-1.3.9/src/drivers/berniw/berniw.xml
berniw_definition="${check_root}/torcs-1.3.9/src/drivers/berniw/berniw.xml"
index_nine_line="$(grep -n '<section name="9">' "${berniw_definition}" | cut -d: -f1)"
index_ten_line="$(grep -n '<section name="10">' "${berniw_definition}" | cut -d: -f1)"
sed -n "${index_nine_line},${index_ten_line}p" "${berniw_definition}" | \
    grep -q '<attstr name="car name" val="car7-trb1"/>'

if [[ -n "${TORCS_PREFIX:-}" ]]; then
    installed="${TORCS_PREFIX}/share/games/torcs/config/raceman/apexrobotstudy.xml"
    cmp --silent "${PRESET_FILE}" "${installed}"
fi

echo "Unattended robot study preset contract passed"
