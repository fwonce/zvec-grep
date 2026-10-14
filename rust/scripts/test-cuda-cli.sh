#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}/.."

target_dir="${CARGO_TARGET_DIR:-target}"
bundle_dir="${1:-${target_dir}/release/zg-cuda}"
model_cache="${ZVEC_GREP_TEST_MODEL_CACHE:-${ZVEC_GREP_MODEL_CACHE:-}}"
if [[ -z "${model_cache}" ]]; then
  echo "ZVEC_GREP_TEST_MODEL_CACHE or ZVEC_GREP_MODEL_CACHE is required" >&2
  exit 2
fi

logs="${ZVEC_GREP_CUDA_TEST_LOGS:-$(mktemp -d)}"
echo "CUDA CLI logs: ${logs}"
exec python3 "${script_dir}/test-cuda-cli.py" \
  --bundle "${bundle_dir}" --model-cache "${model_cache}" --logs "${logs}"
