#!/usr/bin/env python3
"""Build only the CUDA 12.4 plugin from Cargo's locked llama.cpp sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_ARCHITECTURES = "75-real;80-real;86-real;89-real;90-real;90-virtual"


def configure(source, build, cuda_root, architectures):
    nvcc = cuda_root / "bin" / ("nvcc.exe" if os.name == "nt" else "nvcc")
    args = ["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=ON",
            "-DGGML_BACKEND_DL=ON", "-DGGML_NATIVE=OFF", "-DGGML_CPU=OFF",
            "-DGGML_CUDA=ON", "-DGGML_CUDA_NCCL=OFF", "-DGGML_OPENMP=OFF",
            "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF",
            "-DLLAMA_BUILD_TOOLS=OFF", "-DLLAMA_BUILD_COMMON=OFF",
            f"-DCMAKE_CUDA_COMPILER={nvcc}", f"-DCUDAToolkit_ROOT={cuda_root}",
            f"-DCMAKE_CUDA_ARCHITECTURES={architectures}"]
    if os.name == "nt":
        args += ["-DCMAKE_C_COMPILER=cl", "-DCMAKE_CXX_COMPILER=cl",
                 "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL"]
    return args


def build_plugin(bundle, cuda_root, target):
    nvcc = cuda_root / "bin" / ("nvcc.exe" if os.name == "nt" else "nvcc")
    version = subprocess.check_output([str(nvcc), "--version"], encoding="utf-8")
    if not re.search(r"release 12\.4,", version):
        raise RuntimeError("The fallback plugin must be compiled with CUDA 12.4")
    if not (bundle / ("zg.exe" if os.name == "nt" else "zg")).is_file():
        raise RuntimeError("Build the shared primary bundle before the fallback plugin")
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1"], cwd=WORKSPACE, encoding="utf-8"))
    package = next(p for p in metadata["packages"] if p["name"] == "llama-cpp-sys-2")
    source = Path(package["manifest_path"]).parent / "llama.cpp"
    architectures = os.environ.get("CMAKE_CUDA_ARCHITECTURES") or DEFAULT_ARCHITECTURES
    configuration = configure(source, target, cuda_root, architectures)
    key = hashlib.sha256((package["id"] + version + repr(configuration)).encode()).hexdigest()[:16]
    build = target / "ggml-cuda12" / key
    subprocess.run(configure(source, build, cuda_root, architectures), check=True)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--target", "ggml-cuda",
                    "--parallel", os.environ.get("CMAKE_BUILD_PARALLEL_LEVEL", "2")], check=True)
    filename = "ggml-cuda.dll" if os.name == "nt" else "libggml-cuda.so"
    libraries = [p for p in build.rglob(filename) if p.is_file() and not p.is_symlink()]
    if len(libraries) != 1:
        raise RuntimeError(f"Expected exactly one fallback plugin, found {libraries}")
    destination = bundle / "cuda12" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(libraries[0], destination)
    if os.name != "nt":
        subprocess.run(["patchelf", "--set-rpath", "$ORIGIN/..", str(destination)], check=True)
    # Deliberately do not copy the secondary ggml-base/llama/CPU libraries.
    print(f"CUDA 12.4 fallback plugin: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--cuda-root", type=Path, default=Path("/usr/local/cuda"))
    parser.add_argument("--target-dir", type=Path, default=Path(os.environ.get("CARGO_TARGET_DIR", "target")))
    args = parser.parse_args()
    build_plugin(args.bundle.resolve(), args.cuda_root.resolve(), args.target_dir.resolve())
