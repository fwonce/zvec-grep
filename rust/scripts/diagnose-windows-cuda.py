#!/usr/bin/env python3
"""Replay an existing package; isolate cases and inspect live processes on timeout."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location("cuda_cli", Path(__file__).with_name("test-cuda-cli.py"))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def capture(command, destination, timeout=20):
    """Bound diagnostic commands too; retain partial output when they fail."""
    with destination.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False)
            stream.write(f"\nDiagnostic exit code: {result.returncode}\n")
            return result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(f"\nDiagnostic failed: {error}\n")
            return -1


def inspect_hang(process, logs, stage, cdb):
    # Observe the exact child PID before terminating it. Never attach by process name.
    for sample in range(2):
        if process.poll() is not None:
            break
        capture(["nvidia-smi", "-q"], logs / f"{stage}.hang-{sample}.gpu.txt")
        capture([str(cdb), "-pv", "-p", str(process.pid), "-y", "",
                 "-c", ".echo THREAD_STACKS; ~* kb; .echo LOADED_MODULES; lmv; q"],
                logs / f"{stage}.hang-{sample}.stacks.txt", timeout=45)
        if sample == 0:
            time.sleep(3)


def execute(binary, workspace, logs, stage, arguments, env, *, concurrency, timeout, cdb):
    arguments = list(arguments)
    if "--embedding-concurrency" in arguments:
        arguments[arguments.index("--embedding-concurrency") + 1] = str(concurrency)
    command = [str(binary), *arguments]
    (logs / f"{stage}.command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    print(f"START {stage} concurrency={concurrency}: {command}", flush=True)
    stdout_path, stderr_path = logs / f"{stage}.stdout", logs / f"{stage}.stderr"
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        with subprocess.Popen(command, cwd=workspace, env=env, stdout=out, stderr=err) as process:
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        print(f"TIMEOUT {stage} pid={process.pid}; capturing GPU + native stacks", flush=True)
                        inspect_hang(process, logs, stage, cdb)
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        process.wait(timeout=min(30, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = int(time.monotonic() - started)
                        print(f"PROGRESS {stage} pid={process.pid} elapsed={elapsed}s", flush=True)
                        capture(["nvidia-smi", "--query-gpu=timestamp,name,driver_version,utilization.gpu,memory.used,memory.total",
                                 "--format=csv"], logs / f"{stage}.gpu-{elapsed}.txt", timeout=10)
                        tail = stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
                        print("\n".join(tail), flush=True)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=15)
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    if process.returncode:
        raise AssertionError(f"{stage} exited {process.returncode}\n{stderr[-6000:]}")
    return stdout, stderr


def case_path(path, backend, cuda13):
    if backend == "gguf":
        return path
    # Both ONNX comparisons get the identical CUDA-12-only DLL search path.
    return os.pathsep.join(p for p in path.split(os.pathsep)
                           if not (cuda13 and p.strip('"').casefold().startswith(cuda13.rstrip('\\/').casefold())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--cdb", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if os.name != "nt" or not args.cdb.is_file() or args.timeout <= 0:
        parser.error("Windows, an installed CDB and a positive timeout are required")
    args.logs.mkdir(parents=True, exist_ok=True)
    args.model_cache.mkdir(parents=True, exist_ok=True)
    results = {}
    original_path = os.environ["PATH"]
    cases = (("gguf-cuda13", "gguf", "local/embeddinggemma-300m", "13.1", 2),
             ("onnx-c1", "onnx", "local/all-minilm-l6-v2", "12.4", 1),
             ("onnx-c2", "onnx", "local/all-minilm-l6-v2", "12.4", 2))
    try:
        for name, backend, model, cuda, concurrency in cases:
            logs = (args.logs / name).resolve()
            logs.mkdir(parents=True, exist_ok=True)
            os.environ["PATH"] = case_path(original_path, backend, os.environ.get("CUDA_PATH_V13_1", ""))
            # No secrets or whole environment dumps; retain only runtime search paths.
            (logs / "runtime-path.txt").write_text(os.environ["PATH"], encoding="utf-8")
            cli.run_zg = lambda *a, concurrency=concurrency: execute(
                *a, concurrency=concurrency, timeout=args.timeout, cdb=args.cdb)
            try:
                with tempfile.TemporaryDirectory(prefix=f"zg-{name}-") as work:
                    if backend == "onnx":
                        assert not any((Path(p.strip('"')) / "cublas64_13.dll").is_file()
                                       for p in os.environ["PATH"].split(os.pathsep) if p), "CUDA 13 DLL path leaked"
                    evidence = cli.run_case((args.bundle / "zg.exe").resolve(), args.model_cache.resolve(),
                                            Path(work), logs, backend, model, cuda)
                    results[name] = {"status": "passed", "concurrency": concurrency, "evidence": evidence}
            except Exception as error:
                results[name] = {"status": "failed", "concurrency": concurrency, "error": str(error)}
                print(f"FAILED {name}: {error}", flush=True)
            (args.logs / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"CASE RESULT {name}: {results[name]['status']}", flush=True)
    finally:
        os.environ["PATH"] = original_path
    if any(result["status"] != "passed" for result in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
