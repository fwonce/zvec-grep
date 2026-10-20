#!/usr/bin/env python3
"""Stage llama runtime files selected by this Cargo invocation, never a glob.

SDK selection, download and integrity checks belong to the pinned sys crate.
This only relocates runtime files for zg; SDK bindings/import archives are not
end-user payload. Source builds keep their existing CUDA 12 fallback builder.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as payload:
        for block in iter(lambda: payload.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def cargo_records(messages):
    return [json.loads(line) for line in Path(messages).read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def backend_directory(records):
    # This is the safe crate's public BACKENDS_DIR contract, also emitted for
    # fresh Cargo builds. It points either into a verified SDK or source OUT_DIR.
    directories = {Path(value) for record in records
                   if record.get("reason") == "build-script-executed"
                   and "llama-cpp-2" in record.get("package_id", "")
                   for name, value in record.get("env", []) if name == "GGML_BACKENDS_DIR"}
    if len(directories) != 1:
        raise RuntimeError(f"Expected one active llama backend directory from Cargo: {directories}")
    return directories.pop()


def stage_runtime(messages, destination):
    records = cargo_records(messages)
    backend = backend_directory(records)
    windows = (backend / "ggml-cuda.dll").is_file()
    plugin = "ggml-cuda.dll" if windows else "libggml-cuda.so"
    if not (backend / plugin).is_file():
        raise RuntimeError(f"Missing selected CUDA plugin: {backend / plugin}")
    manifest_path = backend.parent / "manifest.json"
    manifest = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        architectures = os.environ.get("CUDA12_ARCHITECTURES")
        if architectures and architectures != manifest["fallback_cmake"]["CMAKE_CUDA_ARCHITECTURES"]:
            raise RuntimeError("CUDA12_ARCHITECTURES differs from the SDK; use LLAMA_CPP_BUILD_MODE=source")
    elif os.environ.get("LLAMA_CPP_BUILD_MODE") in ("prebuilt", "external"):
        raise RuntimeError("Required prebuilt runtime manifest is missing")

    if manifest:
        core_dirs = [backend]
    else:
        outputs = {Path(record["out_dir"]) for record in records
                   if record.get("reason") == "build-script-executed"
                   and "llama-cpp-sys-2" in record.get("package_id", "")}
        if len(outputs) != 1:
            raise RuntimeError(f"Expected one active llama sys OUT_DIR: {outputs}")
        output = outputs.pop()
        core_dirs = [output / suffix for suffix in ("lib", "lib64", "bin", "build/bin", "build/bin/Release")]

    files = {}
    for stem in ("llama", "ggml", "ggml-base"):
        pattern = f"{stem}.dll" if windows else f"lib{stem}.so*"
        candidates = sorted({p.resolve() for directory in core_dirs for p in directory.glob(pattern)
                             if p.is_file()})
        if not candidates:
            raise RuntimeError(f"Missing selected core library: {pattern}")
        # SDKs contain identical copies of Unix linker-name/SONAME aliases.
        if len({digest(p) for p in candidates}) != 1:
            raise RuntimeError(f"Conflicting core library aliases: {candidates}")
        source = candidates[0]
        name = source.name
        if not windows:
            dynamic = subprocess.check_output(["readelf", "-d", str(source)], text=True)
            soname = re.search(r"\(SONAME\).*\[([^]]+)\]", dynamic)
            if not soname or "/" in soname[1] or "\\" in soname[1]:
                raise RuntimeError(f"Invalid or missing SONAME: {source}")
            name = soname[1]
        files[name] = source
    for source in backend.iterdir():
        if source.is_file() and re.fullmatch(
                r"ggml-(?:cpu(?:-[\w-]+)?|cuda)\.dll" if windows
                else r"libggml-(?:cpu(?:-[\w-]+)?|cuda)\.so(?:\.\d+)*", source.name):
            files[source.name] = source
    if not any("ggml-cpu" in name for name in files):
        raise RuntimeError("Selected runtime has no CPU backend")
    if manifest:
        fallback = backend / "cuda12" / plugin
        if not fallback.is_file():
            raise RuntimeError(f"SDK is missing its CUDA 12 fallback: {fallback}")
        files[f"cuda12/{plugin}"] = fallback

    for name, source in files.items():
        if manifest:
            relative = source.relative_to(backend.parent).as_posix()
            if digest(source) != manifest["files"].get(relative):
                raise RuntimeError(f"SDK runtime payload changed after Cargo verification: {relative}")
        target = Path(destination) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f"Staged {len(files)} llama runtime files from {'verified SDK' if manifest else 'source build'}: {backend}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cargo-messages", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    stage_runtime(args.cargo_messages, args.destination)
