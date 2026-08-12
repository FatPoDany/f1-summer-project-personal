#!/usr/bin/env bash
# Download and verify IBM's official Granite 4.1 3B Q4_K_M GGUF.

set -euo pipefail

MODEL_REPO="ibm-granite/granite-4.1-3b-GGUF"
MODEL_REVISION="ab4701481089b58a082ef63cc1cee738887293ff"
MODEL_FILE="granite-4.1-3b-Q4_K_M.gguf"
MODEL_SIZE="2099501664"
MODEL_SHA256="662b0626cd58f443baea23559b469df6576a81d349649c59413b36a9fb32eb29"
MODEL_CACHE="${GRANITE_MODEL_CACHE:-${TMPDIR:-/tmp}/apex-granite-models}"
MODEL_PATH="${GRANITE_MODEL_PATH:-${MODEL_CACHE}/${MODEL_FILE}}"
DOWNLOAD_PATH="${MODEL_PATH}.part"
MODEL_URL="https://huggingface.co/${MODEL_REPO}/resolve/${MODEL_REVISION}/${MODEL_FILE}"

mkdir -p "$(dirname "${MODEL_PATH}")"

verify_model() {
    local candidate="$1"
    [[ -f "${candidate}" ]] || return 1
    local actual_size actual_sha256
    actual_size="$(stat -c '%s' "${candidate}")"
    [[ "${actual_size}" == "${MODEL_SIZE}" ]] || return 1
    actual_sha256="$(sha256sum "${candidate}" | awk '{print $1}')"
    [[ "${actual_sha256}" == "${MODEL_SHA256}" ]]
}

if verify_model "${MODEL_PATH}"; then
    echo "Verified Granite model already present: ${MODEL_PATH}"
    exit 0
fi

if [[ -f "${MODEL_PATH}" ]]; then
    echo "Refusing unverified model file: ${MODEL_PATH}" >&2
    echo "Expected ${MODEL_SIZE} bytes and SHA-256 ${MODEL_SHA256}." >&2
    exit 2
fi

echo "Downloading ${MODEL_REPO}@${MODEL_REVISION}/${MODEL_FILE}"
echo "Destination: ${MODEL_PATH} (about 2.1 GB)"
curl --fail --location --show-error \
    --retry 5 --retry-delay 2 --continue-at - \
    --output "${DOWNLOAD_PATH}" "${MODEL_URL}"

if ! verify_model "${DOWNLOAD_PATH}"; then
    echo "Downloaded model failed size or SHA-256 verification: ${DOWNLOAD_PATH}" >&2
    exit 1
fi
mv "${DOWNLOAD_PATH}" "${MODEL_PATH}"
echo "Verified Granite model: ${MODEL_PATH}"
echo "SHA-256: ${MODEL_SHA256}"
