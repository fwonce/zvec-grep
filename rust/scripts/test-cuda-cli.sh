#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}/.."

target_dir="${CARGO_TARGET_DIR:-target}"
if [[ "${target_dir}" != /* ]]; then
  target_dir="${PWD}/${target_dir}"
fi
bundle_dir="${1:-${target_dir}/release/zg-cuda}"
zg="${bundle_dir}/zg"
model_cache="${ZVEC_GREP_TEST_MODEL_CACHE:-${ZVEC_GREP_MODEL_CACHE:-}}"

if [[ ! -x "${zg}" ]]; then
  echo "CUDA bundle executable is unavailable: ${zg}" >&2
  exit 2
fi
if [[ -z "${model_cache}" ]]; then
  echo "ZVEC_GREP_TEST_MODEL_CACHE or ZVEC_GREP_MODEL_CACHE is required" >&2
  exit 2
fi

test_dir="$(mktemp -d)"
trap 'rm -rf -- "${test_dir}"' EXIT

run_zg() {
  local working_dir="$1"
  local stdout_file="$2"
  local stderr_file="$3"
  shift 3
  if ! (
    cd "${working_dir}"
    "${zg}" "$@"
  ) >"${stdout_file}" 2>"${stderr_file}"; then
    echo "zg command failed: $*" >&2
    sed 's/^/[stdout] /' "${stdout_file}" >&2
    sed 's/^/[stderr] /' "${stderr_file}" >&2
    return 1
  fi
}

assert_contains() {
  local file="$1"
  local expected="$2"
  if ! grep -Fq -- "${expected}" "${file}"; then
    echo "expected output not found: ${expected}" >&2
    sed 's/^/[output] /' "${file}" >&2
    return 1
  fi
}

assert_cuda_without_fallback() {
  local file="$1"
  local selected="$2"
  assert_contains "${file}" "${selected}"
  if grep -Fqi -- "falling back to CPU" "${file}"; then
    echo "CUDA execution unexpectedly fell back to CPU" >&2
    sed 's/^/[stderr] /' "${file}" >&2
    return 1
  fi
}

run_case() {
  local label="$1"
  local model="$2"
  local selected="$3"
  local case_dir="${test_dir}/${label}"
  local workspace="${case_dir}/workspace"
  local logs="${case_dir}/logs"
  mkdir -p "${workspace}" "${logs}" "${case_dir}/config"
  printf '%s\n' \
    '# GPU validation fixture' \
    'The heliotrope capacitor calibrates the observatory telescope.' \
    'cuda_cli_e2e_marker' >"${workspace}/gpu-fixture.md"

  export XDG_CONFIG_HOME="${case_dir}/config"
  export ZVEC_GREP_HOME="${case_dir}/runtime"

  run_zg "${workspace}" "${logs}/index.stdout" "${logs}/index.stderr" \
    --index --mode direct --embedding "${model}" --device cuda \
    --model-cache "${model_cache}" --embedding-concurrency 2 --debug --no-color
  assert_contains "${logs}/index.stderr" 'Index complete'
  assert_cuda_without_fallback "${logs}/index.stderr" "${selected}"

  run_zg "${workspace}" "${logs}/status.stdout" "${logs}/status.stderr" \
    --status --mode direct --check-ready --no-color
  assert_contains "${logs}/status.stdout" 'Workspace index is ready'
  assert_contains "${logs}/status.stdout" "Embedding   ${model}"

  run_zg "${workspace}" "${logs}/query.stdout" "${logs}/query.stderr" \
    --mode direct --vector 'Which component calibrates the telescope?' \
    --device cuda --model-cache "${model_cache}" --refresh off --limit 1 \
    --preview full --debug --no-color
  assert_contains "${logs}/query.stdout" 'gpu-fixture.md'
  assert_contains "${logs}/query.stdout" 'cuda_cli_e2e_marker'
  assert_cuda_without_fallback "${logs}/query.stderr" "${selected}"

  echo "${label}: CLI index, status, and vector query used CUDA"
  grep -F -- "${selected}" "${logs}/index.stderr" "${logs}/query.stderr"
}

run_case \
  llama-cpp \
  local/embeddinggemma-300m \
  'llama.cpp GPU embedding backend selected'
run_case \
  onnx-runtime \
  local/all-minilm-l6-v2 \
  'Transformers ONNX cuda embedding execution provider selected'

echo 'CUDA CLI end-to-end validation passed'
