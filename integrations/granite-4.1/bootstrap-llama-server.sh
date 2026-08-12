#!/usr/bin/env bash
# Build a pinned CPU llama-server. Model weights are not downloaded here.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LLAMA_TAG="b10355"
LLAMA_COMMIT="dd1ea524333b1e697489067d7a4c39c60d32beee"
SOURCE_DIR="${LLAMA_CPP_SOURCE:-${TMPDIR:-/tmp}/apex-llama.cpp-${LLAMA_TAG}}"
INSTALL_DIR="${LLAMA_CPP_INSTALL:-${REPO_ROOT}/.tools/llama.cpp-${LLAMA_TAG}}"

# llama.cpp uses modern C++17 template features that Rocky 8's stock GCC 8
# cannot compile. Prefer the university host's newer user-visible toolchain;
# callers on other systems can set LLAMA_CC and LLAMA_CXX explicitly.
llama_cc="${LLAMA_CC:-}"
llama_cxx="${LLAMA_CXX:-}"
if [[ -z "${llama_cc}" || -z "${llama_cxx}" ]]; then
    for compiler_root in \
        /opt/gcc/13.4.0/bin \
        /opt/rh/gcc-toolset-14/root/usr/bin \
        /opt/rh/gcc-toolset-10/root/usr/bin \
        /usr/bin; do
        if [[ -x "${compiler_root}/gcc" && -x "${compiler_root}/g++" ]]; then
            candidate_major="$("${compiler_root}/g++" -dumpversion | cut -d. -f1)"
            if [[ "${candidate_major}" -ge 10 ]]; then
                llama_cc="${compiler_root}/gcc"
                llama_cxx="${compiler_root}/g++"
                break
            fi
        fi
    done
fi
if [[ ! -x "${llama_cc}" || ! -x "${llama_cxx}" ]]; then
    echo "llama.cpp ${LLAMA_TAG} needs GCC 10+ (or a comparable Clang)." >&2
    echo "Set LLAMA_CC and LLAMA_CXX to a modern compiler." >&2
    exit 2
fi
compiler_major="$("${llama_cxx}" -dumpversion | cut -d. -f1)"
BUILD_DIR="${SOURCE_DIR}/build-apex-gcc-${compiler_major}"

if [[ -x "${INSTALL_DIR}/bin/llama-server" ]]; then
    installed_version="$("${INSTALL_DIR}/bin/llama-server" --version 2>&1 || true)"
    if [[ "${installed_version}" == *"dd1ea52"* ]]; then
        echo "llama-server already installed: ${INSTALL_DIR}/bin/llama-server"
        exit 0
    fi
    echo "Refusing an existing llama-server with an unexpected revision:" >&2
    echo "${installed_version}" >&2
    exit 2
fi

if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
    if [[ -e "${SOURCE_DIR}" ]]; then
        echo "Refusing non-git source path: ${SOURCE_DIR}" >&2
        exit 2
    fi
    git clone --depth 1 --branch "${LLAMA_TAG}" \
        https://github.com/ggml-org/llama.cpp.git "${SOURCE_DIR}"
fi

actual_commit="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${LLAMA_COMMIT}" ]]; then
    echo "Unexpected llama.cpp revision: ${actual_commit}" >&2
    echo "Expected ${LLAMA_COMMIT} (${LLAMA_TAG})" >&2
    exit 2
fi
actual_origin="$(git -C "${SOURCE_DIR}" remote get-url origin)"
if [[ "${actual_origin}" != "https://github.com/ggml-org/llama.cpp.git" ]]; then
    echo "Unexpected llama.cpp origin: ${actual_origin}" >&2
    exit 2
fi
if ! git -C "${SOURCE_DIR}" diff --quiet ||
   ! git -C "${SOURCE_DIR}" diff --cached --quiet; then
    echo "Refusing a modified llama.cpp source tree: ${SOURCE_DIR}" >&2
    exit 2
fi

echo "Compiler: $("${llama_cxx}" --version | head -1)"
cmake -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="${llama_cc}" \
    -DCMAKE_CXX_COMPILER="${llama_cxx}" \
    -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++" \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_DIR}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_CUDA=OFF \
    -DGGML_CCACHE=OFF \
    -DGGML_NATIVE=ON \
    -DLLAMA_BUILD_UI=OFF \
    -DLLAMA_BUILD_SERVER=ON \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF
cmake --build "${BUILD_DIR}" --target llama-server \
    -j"${LLAMA_BUILD_JOBS:-8}"
install -d "${INSTALL_DIR}/bin"
install -m 0755 "${BUILD_DIR}/bin/llama-server" "${INSTALL_DIR}/bin/llama-server"

echo "llama-server installed: ${INSTALL_DIR}/bin/llama-server"
