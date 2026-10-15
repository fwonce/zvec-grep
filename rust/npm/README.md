# npm distribution

The release layout has one small meta package and one native package for each
published target. `platforms.json` is the single source of truth for package
names and npm `os`, `cpu`, and `libc` selectors.

The current published targets are macOS ARM64/x64, Linux x64/ARM64 with glibc,
and Windows x64.

The platform package owns the complete runtime payload:

```text
bin/zg[.exe]
lib/libzvec_c_api.*       # storage runtime and lib/data dictionaries
lib/lib{llama,ggml}*      # CUDA variant: shared llama.cpp core and CPU backend
lib/*onnxruntime*        # ONNX Runtime core/providers, when dynamically linked
lib/*ggml-cuda*          # primary CUDA 13.1 GGUF plugin
lib/cuda12/*ggml-cuda*    # CUDA 12.4 GGUF fallback plugin
checksums.json
LICENSES/
SBOM.spdx.json
```

`bin/` contains only the native executable. All runtime libraries, providers,
plugins, and dictionaries live under `lib/`. Unix binaries use a `../lib`
rpath. Windows uses a native MSVC delay-load hook to resolve DLLs from the
executable's sibling `lib/`. The binary runs directly, without a JavaScript
launcher or user-provided loader environment variables.

Non-CUDA builds enable ORT's upstream `copy-dylibs` feature to stage runtime
sidecars beside the Cargo executable. npm packaging copies them into `lib/`
as regular files, including any symlinked libraries. This includes Intel macOS's
dynamically linked ORT and Windows DirectML's DLL; it does not change the static
ORT linkage used by the default macOS ARM64 and Linux CPU builds. CUDA bundles
load ORT dynamically and continue to stage their pinned core/providers explicitly.

The default `linux-x64-gnu` and `win32-x64-msvc` native packages are CPU/CUDA
bundles. CUDA libraries remain runtime plugins: compatible NVIDIA libraries
enable GPU use, while hosts without them continue on CPU. Release maintainers
may use `--cpu-only` for a CPU-only build, but that variant is not
published as the default x64 package on Linux or Windows.

`npm run build` and release packaging prefer CUDA on those two targets when
`nvcc` from CUDA 13.1 is available. If `nvcc` is absent they build a CPU-only
package instead. An installed but unsupported CUDA toolkit is an error; use
`--cpu-only` deliberately rather than silently producing a different GPU ABI.
The full CUDA build also needs the 12.4 toolkit for the fallback plugin; an
incomplete CUDA build fails and does not silently publish a CPU package.
CPU/CUDA build trees are isolated under `target/cpu` and `target/cuda` on these
targets. `--no-build` reuses the last successful recorded build, not a stale
bundle selected by its existence. Explicit CI bundles use `--bundle-dir`.
Plain `cargo build --release -p zg` remains a non-CUDA developer build.

Published target/backend support is:

| Target | llama.cpp / GGUF | ONNX Runtime |
| --- | --- | --- |
| macOS ARM64 | CPU + Metal | CPU (CoreML deliberately not selected) |
| macOS x64 (14+) | CPU + Metal where available | CPU (CoreML deliberately not selected) |
| Linux x64 glibc (Ubuntu 24.04 baseline) | CPU + CUDA 13.1, CUDA 12.4 fallback | CPU + CUDA 12 |
| Linux ARM64 glibc | CPU | CPU |
| Windows x64 | CPU + CUDA 13.1, CUDA 12.4 fallback | CPU + CUDA 12 |

A deliberate CPU-only Windows build retains DirectML. The published Windows
CUDA bundle uses the official ONNX Runtime 1.28.0 CUDA 12 provider and does not
claim DirectML support. ORT requires CUDA 12 runtime/cuBLAS and cuDNN 9 even
when GGUF uses CUDA 13; the two runtime families can coexist. NVIDIA driver,
CUDA runtime, cuBLAS and cuDNN remain host dependencies, not bundled libraries.
macOS keeps its existing ONNX CPU routing because of CoreML regressions.
Linux musl, Windows ARM64, CUDA on ARM64/macOS and Vulkan packages are not in
the release matrix. CUDA builds target SM 75/80/86/89/90 plus compute_90 PTX;
this is not a claim that every newer GPU has been tested.

The meta package locks every platform package to its exact version through
`optionalDependencies`. Its `postinstall` selects the installed package,
validates package identity and every payload checksum, and materializes the
native `bin/` and `lib/` payload. The npm command is always named `zg`; the meta
package retains the cross-platform `bin/zg.exe` native entry point so npm's
Windows shim and Unix symlink both enter the executable directly.

The published meta package supports Node.js 14.14 and newer for installation
only, not for CLI execution. The root workspace may require a newer Node version for
development and package tests without reducing published compatibility.

Release order:

1. Build and package every native target; apply ad hoc signatures on macOS.
2. Verify payload closure, checksums, licenses, and SBOMs.
3. Publish all native packages to `rust-canary`, then publish the meta package
   to `rust-canary` last. Candidate publication never changes `latest`.
4. On every native runner, install the exact meta-package version from the
   registry alone, so npm must select its native optional dependency.
5. Verify CLI startup, managed-rg, daemon startup and IPC, then update from a
   distinct published baseline version and roll back to that version.
6. Promote native `latest` tags and then the meta `latest` tag in a separate job,
   only after all five registry install/update/rollback jobs pass.

`.github/workflows/rust-publish.yml` builds and installs every target on its
native runner, checks CLI startup, managed-rg and actual daemon IPC, and collects
the candidates into a downloadable artifact. Pushes to `main` and
`release/rust-*`, and manual runs with `publish=false, cuda=false`, use ordinary CPU runners;
they do not allocate GPUs or publish packages. CUDA compilation still runs
for the default Linux/Windows x64 bundles and does not require GPU hardware.
Manual `cuda=true` runs switch these two targets to GPU runners and enable
package-level GPU end-to-end validation even with `publish=false`. Publishing
also enables GPU validation unconditionally, even if `cuda` was not selected.
Intel macOS installation and runtime checks must pass before the other four
platform builds start. Its ONNX Runtime with CoreML and zvec SDK are built from
pinned sources, cached, and bundled with the native package. The Intel release
targets macOS 14 and later.

After collection, independent Linux/Windows CPU jobs download and install the
exact native/meta tarballs offline, without rebuilding. They verify checksums,
CLI startup, daemon IPC, and real GGUF/ONNX indexing and vector queries with
explicit CPU selection and default CPU fallback, without CUDA host libraries.
These checks also run for unpublished candidates and must pass before npm
publication; failures retain the candidate and `cpu-artifact-evidence-*` logs.

During publication, Linux x64 and Windows x64 compile and run CLI GPU tests on
the hosted T4 runners. The separate manual `rust-cuda-ci.yml` checks and tests
the CUDA-enabled workspace, then reuses its compiled test binary for real ORT
and llama.cpp GPU model tests without packaging or publishing.
Both packages share one llama/ggml core and include CUDA 13.1 and CUDA 12.4 GGUF
plugins. The hash-verified official ONNX Runtime CUDA 12 core/providers are
shared-library sidecars. The Windows CUDA package supports CPU and NVIDIA CUDA,
not DirectML; the non-CUDA Windows build retains its existing DirectML runtime.

Explicit publication runs from a `rust-v<version>`-tagged commit on `main` and
uses npm provenance with trusted publishing or `NPM_TOKEN`. Native packages are
published first and the meta package last, always under `rust-canary`. Selecting
`latest` requests the separate promotion job; any registry smoke failure leaves
the existing default installation unchanged.

Publication is currently blocked until reviewed license texts and SPDX SBOMs
exist for every native target under [`compliance/`](compliance/README.md).
Candidate artifacts can still be downloaded, but they are not publication-ready
while these inputs are missing. Both the initial source check and the tarball
preflight fail before publication when compliance inputs are incomplete.

Publication and promotion explicitly install npm 11.21.0, which supports OIDC
authentication for `npm dist-tag`. Configure trusted publishing for all six
packages with repository `zvec-ai/zvec-grep`, workflow filename
`rust-publish.yml`, and both **Allow npm publish** and **Allow npm dist-tag**.
The dist-tag permission is independent and disabled by default; `id-token:
write` alone does not grant it. See the [npm trusted publishing documentation](https://docs.npmjs.com/trusted-publishers/#managing-dist-tags-with-trusted-publishing).
An eligible `NPM_TOKEN` remains an alternative authentication method.

Developer certificate signing and Apple notarization can be added to the native
build steps independently of npm publishing.
