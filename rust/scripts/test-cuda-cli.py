#!/usr/bin/env python3
"""Exercise the packaged CLI on a real CUDA GPU (Linux and Windows)."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


MODELS = (
    ("gguf", "local/embeddinggemma-300m"),
    ("onnx", "local/all-minilm-l6-v2"),
)
MARKER = "cuda_cli_e2e_marker"


def cuda_evidence(stderr, backend, expected_gguf_cuda=None):
    """Selection alone is insufficient for ORT: require CUDA graph placement."""
    if re.search(r"fall(?:ing)? back to CPU", stderr, re.IGNORECASE):
        raise AssertionError("CUDA execution fell back to CPU")
    if backend == "gguf":
        if expected_gguf_cuda and not any(
            "llama.cpp CUDA backend library selected" in line
            and re.search(r'cuda_version=["\']?' + re.escape(expected_gguf_cuda) + r'\b', line)
            for line in stderr.splitlines()
        ):
            raise AssertionError(f"Expected GGUF CUDA {expected_gguf_cuda} plugin was not selected")
        lines = [line for line in stderr.splitlines()
                 if "llama.cpp GPU embedding backend selected" in line
                 and re.search(r'backend=["\']?CUDA\b', line, re.IGNORECASE)]
        offload = [line for line in stderr.splitlines()
                   if re.search(r"offloaded [1-9]\d*/\d+ layers to GPU", line)]
        if not offload:
            raise AssertionError("GGUF did not report any layers offloaded to GPU")
        if not lines:
            raise AssertionError("GGUF offload must use CUDA, not another GPU backend")
        lines += offload
    else:
        if "Transformers ONNX cuda embedding execution provider selected" not in stderr:
            raise AssertionError("ONNX CUDA execution provider was not selected")
        lines = [line for line in stderr.splitlines()
                 if re.search(r"placed on \[CUDAExecutionProvider\]\. Number of nodes: [1-9]\d*", line)]
    if not lines:
        raise AssertionError(f"No actual {backend} CUDA execution evidence")
    return lines


def require(text, expected):
    if expected not in text:
        raise AssertionError(f"Expected output not found: {expected}")


def run_zg(binary, workspace, logs, stage, args, env):
    command = [str(binary), *args]
    print(f"Running {stage}: {command}", flush=True)
    # Files, rather than PIPE, retain partial output even on timeout/crash.
    stdout_path = logs / f"{stage}.stdout"
    stderr_path = logs / f"{stage}.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(command, cwd=workspace, env=env, stdout=stdout,
                                stderr=stderr, timeout=900, check=False)
    out = stdout_path.read_text(encoding="utf-8", errors="replace")
    err = stderr_path.read_text(encoding="utf-8", errors="replace")
    if result.returncode:
        raise AssertionError(f"{stage} exited with {result.returncode}\n{out}\n{err[-24000:]}")
    return out, err


def run_case(binary, model_cache, work, logs, backend, model, expected_gguf_cuda):
    workspace = work / backend / "workspace"
    workspace.mkdir(parents=True)
    logs.mkdir(parents=True)
    (workspace / "gpu-fixture.md").write_text(
        "# GPU validation fixture\n"
        "The heliotrope capacitor calibrates the observatory telescope.\n"
        f"{MARKER}\n", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "XDG_CONFIG_HOME": str(work / backend / "config"),
        "ZVEC_GREP_HOME": str(work / backend / "runtime"),
        "ORT_LOG": "verbose",
        "RUST_LOG": "warn,zg=debug,zg_engine=debug,ort=trace,llama-cpp-2=info",
        "NO_COLOR": "1",
    })
    cuda_args = ["--device", "cuda", "--model-cache", str(model_cache), "--debug", "--no-color"]
    _, index_err = run_zg(binary, workspace, logs, "index", [
        "--index", "--name", f"cuda-e2e-{backend}", "--mode", "direct",
        "--embedding", model, "--embedding-concurrency", "2", *cuda_args,
    ], env)
    require(index_err, "Index complete")
    index_evidence = cuda_evidence(index_err, backend, expected_gguf_cuda)
    status, _ = run_zg(binary, workspace, logs, "status", [
        "--status", "--mode", "direct", "--check-ready", "--no-color",
    ], env)
    require(status, "Workspace index is ready")
    require(status, model)
    query, query_err = run_zg(binary, workspace, logs, "query", [
        "--mode", "direct", "--vector", "Which component calibrates the telescope?",
        "--refresh", "off", "--limit", "1", "--preview", "full", *cuda_args,
    ], env)
    require(query, "gpu-fixture.md")
    require(query, MARKER)
    query_evidence = cuda_evidence(query_err, backend, expected_gguf_cuda)
    result = {"model": model, "gguf_cuda": expected_gguf_cuda if backend == "gguf" else None,
              "index": index_evidence, "query": query_evidence}
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--backend", choices=("all", "gguf", "onnx"), default="all")
    parser.add_argument("--expected-gguf-cuda", choices=("12.4", "13.1"), required=True)
    args = parser.parse_args()
    binary = args.bundle.resolve() / ("zg.exe" if os.name == "nt" else "zg")
    if not binary.is_file():
        parser.error(f"packaged CLI is missing: {binary}")
    logs = args.logs.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    args.model_cache.mkdir(parents=True, exist_ok=True)
    gpu = subprocess.check_output([
        "nvidia-smi", "--query-gpu=name,compute_cap,driver_version", "--format=csv,noheader",
    ], encoding="utf-8")
    (logs / "gpu.txt").write_text(gpu, encoding="utf-8")
    print(gpu, flush=True)
    results, failures = {}, []
    with tempfile.TemporaryDirectory(prefix="zg-cuda-e2e-") as temporary:
        for backend, model in MODELS:
            if args.backend not in ("all", backend):
                continue
            try:
                results[backend] = run_case(binary, args.model_cache.resolve(), Path(temporary),
                                            logs / backend, backend, model, args.expected_gguf_cuda)
            except (AssertionError, OSError, subprocess.SubprocessError) as error:
                failures.append(f"{backend}: {error}")
                print(f"FAILED: {failures[-1]}", flush=True)
    (logs / "results.json").write_text(json.dumps({"passed": results, "failures": failures}, indent=2),
                                      encoding="utf-8")
    if failures:
        raise SystemExit("CUDA CLI end-to-end validation failed (see retained logs)")
    print(f"CUDA CLI end-to-end validation passed: {', '.join(results)} index + query", flush=True)


if __name__ == "__main__":
    main()
