#!/usr/bin/env bash
# Start IBM's official Granite 4.1 3B Q4_K_M GGUF on a loopback-only
# OpenAI-compatible llama.cpp endpoint.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LLAMA_TAG="b10355"
SERVER_BIN="${LLAMA_SERVER_BIN:-${REPO_ROOT}/.tools/llama.cpp-${LLAMA_TAG}/bin/llama-server}"
MODEL_ALIAS="${GRANITE_MODEL:-ibm-granite/granite-4.1-3b}"
MODEL_CACHE="${GRANITE_MODEL_CACHE:-${TMPDIR:-/tmp}/apex-granite-models}"
MODEL_FILE="granite-4.1-3b-Q4_K_M.gguf"
MODEL_PATH="${GRANITE_MODEL_PATH:-${MODEL_CACHE}/${MODEL_FILE}}"
MODEL_REVISION="ab4701481089b58a082ef63cc1cee738887293ff"
MODEL_SHA256="662b0626cd58f443baea23559b469df6576a81d349649c59413b36a9fb32eb29"
PORT="${GRANITE_PORT:-8080}"
THREADS="${GRANITE_THREADS:-8}"

if [[ ! -x "${SERVER_BIN}" ]]; then
    echo "llama-server not found: ${SERVER_BIN}" >&2
    echo "Run integrations/granite-4.1/bootstrap-llama-server.sh first." >&2
    exit 2
fi

if [[ ! -f "${MODEL_PATH}" ]]; then
    echo "Verified Granite model not found: ${MODEL_PATH}" >&2
    echo "Run integrations/granite-4.1/download-model.sh first." >&2
    exit 2
fi
actual_sha256="$(sha256sum "${MODEL_PATH}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${MODEL_SHA256}" ]]; then
    echo "Refusing model with unexpected SHA-256: ${actual_sha256}" >&2
    echo "Expected: ${MODEL_SHA256}" >&2
    exit 2
fi

echo "Granite model: ${MODEL_PATH}"
echo "HF revision:   ${MODEL_REVISION}"
echo "GGUF SHA-256:  ${MODEL_SHA256}"
echo "Endpoint:      http://127.0.0.1:${PORT}/v1"
echo "CPU threads:   ${THREADS}"
echo "The server is loopback-only; press Ctrl-C to stop it."

server_args=(
    "${SERVER_BIN}"
    --jinja
    -fa on
    -m "${MODEL_PATH}"
    --alias "${MODEL_ALIAS}"
    --no-mmproj
    --no-ui
    --cors-origins localhost
    --host 127.0.0.1
    --port "${PORT}"
    --ctx-size 4096
    --parallel 1
    --threads "${THREADS}"
)
if [[ -n "${GRANITE_API_KEY:-}" ]]; then
    export LLAMA_API_KEY="${GRANITE_API_KEY}"
    echo "API auth:      enabled (GRANITE_API_KEY)"
else
    echo "API auth:      disabled; set GRANITE_API_KEY on a multi-user host"
fi

exec "${server_args[@]}"
