#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}/.."

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "CUDA builds are supported only on Linux" >&2
  exit 2
fi
for command in cargo cmake nvcc patchelf readelf; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "required command is unavailable: ${command}" >&2
    exit 2
  fi
done

if [[ -z "${ORT_CUDA_VERSION:-}" ]]; then
  ORT_CUDA_VERSION="$(nvcc --version | sed -n 's/.*release \([0-9][0-9]*\)\..*/\1/p' | tail -n 1)"
  if [[ -z "${ORT_CUDA_VERSION}" ]]; then
    echo "unable to detect CUDA major version; set ORT_CUDA_VERSION" >&2
    exit 2
  fi
  export ORT_CUDA_VERSION
fi

if [[ -z "${CMAKE_CUDA_ARCHITECTURES:-}" ]]; then
  # Distribution builds must not inherit the architecture of whichever GPU
  # happens to be attached to the builder. CMake's all-major value emits a
  # relocatable fat binary for every major architecture supported by nvcc,
  # plus forward-compatible PTX for the newest major architecture.
  CMAKE_CUDA_ARCHITECTURES="all-major"
  export CMAKE_CUDA_ARCHITECTURES
fi

if [[ -z "${CMAKE_BUILD_PARALLEL_LEVEL:-}" ]]; then
  CMAKE_BUILD_PARALLEL_LEVEL="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  export CMAKE_BUILD_PARALLEL_LEVEL
fi

cuda_toolkit_version="$(nvcc --version | sed -n 's/.*release \([0-9][0-9.]*\).*/\1/p' | tail -n 1)"
if [[ -z "${cuda_toolkit_version}" ]]; then
  echo "unable to detect the CUDA toolkit version" >&2
  exit 2
fi
echo "CUDA toolkit: ${cuda_toolkit_version} (ORT major ${ORT_CUDA_VERSION})"
echo "CUDA architectures: ${CMAKE_CUDA_ARCHITECTURES}"

target_dir="${CARGO_TARGET_DIR:-target}"
if [[ "${target_dir}" != /* ]]; then
  target_dir="${PWD}/${target_dir}"
fi
build_config="cuda_toolkit=${cuda_toolkit_version};ort_cuda_major=${ORT_CUDA_VERSION};architectures=${CMAKE_CUDA_ARCHITECTURES}"
build_config_file="${target_dir}/.zvec-grep-cuda-build-config"
previous_build_config="$(sed -n '1p' "${build_config_file}" 2>/dev/null || true)"
if [[ "${previous_build_config}" != "${build_config}" && -f "${target_dir}/CACHEDIR.TAG" ]]; then
  echo "CUDA build configuration changed; rebuilding llama-cpp-sys-2"
  cargo clean --target-dir "${target_dir}" --package llama-cpp-sys-2
fi

cargo build --locked --release --package zg --features cuda
printf '%s\n' "${build_config}" > "${build_config_file}"

profile_dir="${target_dir}/release"
bundle_dir="${profile_dir}/zg-cuda"
staging_dir="$(mktemp -d "${profile_dir}/.zg-cuda.XXXXXX")"
trap 'rm -rf -- "${staging_dir}"' EXIT

install -m 0755 "${profile_dir}/zg" "${staging_dir}/zg"

cuda_backend="$(find "${profile_dir}/build" -path '*/llama-cpp-sys-2-*/out/backends/libggml-cuda.so*' -type f -printf '%T@ %p\n' \
  | sort -nr | sed -n '1s/^[^ ]* //p')"
if [[ -z "${cuda_backend}" ]]; then
  echo "unable to locate the llama.cpp CUDA backend module" >&2
  exit 1
fi
backend_dir="$(dirname -- "${cuda_backend}")"
llama_out_dir="$(dirname -- "${backend_dir}")"

shopt -s nullglob
llama_backend_libraries=("${backend_dir}"/libggml-*.so*)
if (( ${#llama_backend_libraries[@]} == 0 )); then
  echo "llama.cpp shared libraries are incomplete" >&2
  exit 1
fi

copy_core_library() {
  local stem="$1"
  local source=""
  local candidate
  for candidate in "${llama_out_dir}"/lib*/"${stem}".so*; do
    if [[ -f "${candidate}" && ! -L "${candidate}" ]]; then
      source="${candidate}"
      break
    fi
  done
  if [[ -z "${source}" ]]; then
    echo "unable to locate the real shared library for ${stem}" >&2
    exit 1
  fi
  local soname
  soname="$(readelf -d "${source}" | sed -n 's/.*(SONAME).*\[\([^]]*\)\].*/\1/p' | head -n 1)"
  if [[ -z "${soname}" || "${soname}" == */* ]]; then
    echo "invalid or missing SONAME for ${source}: ${soname}" >&2
    exit 1
  fi
  cp -L --preserve=mode,timestamps -- "${source}" "${staging_dir}/${soname}"
}

copy_core_library libllama
copy_core_library libggml
copy_core_library libggml-base
cp -L --preserve=mode,timestamps -- "${llama_backend_libraries[@]}" "${staging_dir}/"

for provider in libonnxruntime_providers_shared.so libonnxruntime_providers_cuda.so; do
  provider_path="$(find "${profile_dir}" -maxdepth 2 -name "${provider}" -print -quit)"
  if [[ -z "${provider_path}" ]]; then
    echo "unable to locate ONNX Runtime provider: ${provider}" >&2
    exit 1
  fi
  cp -L --preserve=mode,timestamps -- "${provider_path}" "${staging_dir}/${provider}"
done

if [[ ! -f "${profile_dir}/libzvec_c_api.so" ]]; then
  echo "unable to locate libzvec_c_api.so" >&2
  exit 1
fi
cp -L --preserve=mode,timestamps -- "${profile_dir}/libzvec_c_api.so" "${staging_dir}/"
if [[ -d "${profile_dir}/data" ]]; then
  cp -a -- "${profile_dir}/data" "${staging_dir}/"
fi

while IFS= read -r -d '' file; do
  if readelf -h "${file}" >/dev/null 2>&1; then
    patchelf --set-rpath '$ORIGIN' "${file}"
  fi
done < <(find "${staging_dir}" -maxdepth 1 -type f -print0)

rm -rf -- "${bundle_dir}"
mv -- "${staging_dir}" "${bundle_dir}"
trap - EXIT

bash scripts/check-cuda-linkage.sh "${bundle_dir}"
echo "CUDA bundle: ${bundle_dir}"
