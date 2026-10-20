# npm distribution

The release layout has one small meta package, one base native package for each
published target, and exact-version CUDA component packages for Linux/Windows
x64. `platforms.json` is the single source of truth for package
names and npm `os`, `cpu`, and `libc` selectors.

The current published targets are macOS ARM64/x64, Linux x64/ARM64 with glibc,
and Windows x64.

After installation, the meta package retains the existing runtime paths. CUDA
files are present only after successful plugin installation:

```text
bin/zg.exe              # meta entry on every OS; command remains zg
lib/libzvec_c_api.*       # storage runtime and lib/data dictionaries
lib/lib{llama,ggml}*      # CUDA variant: shared llama.cpp core and CPU backend
lib/*onnxruntime*        # ONNX Runtime core/providers, when dynamically linked
lib/*ggml-cuda*          # primary CUDA 13.1 GGUF plugin
lib/cuda12/*ggml-cuda*    # CUDA 12.4 GGUF fallback plugin
```

Source npm packages also carry `checksums.json` and, once reviewed inputs are
available, `LICENSES/` and `SBOM.spdx.json`; these are package metadata rather
than runtime files copied by the installer.

The Linux x64 base package's `plugins.json` references `-ggml-cuda13`, `-ggml-cuda12`,
`-ort-cuda-part1` and `-ort-cuda-part2` packages. Windows x64 references
`-ggml-cuda13`, `-ggml-cuda12` and `-ort-cuda`. These suffixes extend the base
platform package name. macOS, Linux ARM64 and deliberate CPU-only builds remain
single native packages. A default all-platform release contains 13 tarballs:
five base packages, seven runtime components and the meta package.

The base package retains the executable, CPU/core libraries and dictionaries.
CUDA components contain whole GGML plugins or the ORT provider. The Linux ORT
provider is split into two ordered byte ranges under `parts/`; postinstall
streams them back into the original `.so` and verifies the original complete
file's SHA256. No compiler, model execution or CUDA Toolkit installation is
performed during npm installation. Meta still selects its base by OS/CPU/libc,
but CUDA components are no longer npm dependencies. Postinstall first verifies
and installs CPU, then uses bounded NVIDIA detection to decide whether to fetch
the complete pinned CUDA set. CPU-only hosts make no CUDA component requests.
GPU installs still download the whole set; splitting limits each upload, not
their total size. Downloaded archives may occupy cache space in addition to the
assembled libraries.
Auto detection checks only whether `nvidia-smi` succeeds within five seconds
and returns a GPU. It does not compare the reported driver version, validate
CUDA/cuDNN dependencies, or select a per-backend subset of plugins. GGUF and ORT
still choose their execution backends independently at runtime.

Use `ZG_INSTALL_ACCELERATOR=cpu|auto|cuda` (default `auto`) with npm install or
rebuild. `cpu` skips detection/downloads; `cuda` explicitly requests plugins even
on a GPU-free builder and fails on any plugin error. Auto failures retain CPU
with diagnostics. A forced plugin failure also leaves the CPU core intact.
Later use `ZG_INSTALL_ACCELERATOR=cuda npm rebuild -g @zvec/zvec-grep` (omit `-g`
for project-local installs). Stop zg first and restart the daemon afterwards.
There is no extra setup command or runtime downloading.

`ZG_PLUGIN_DIR` is an authoritative absolute directory of exact-version `.tgz`
files. `ZG_INSTALL_OFFLINE=1`/npm offline forbids network requests;
`ZG_PLUGIN_CACHE` overrides the content-addressed download cache. Missing local
files never fall back to a registry version. npm handles registry, proxy, auth
and custom CA settings for online transport, with component scripts disabled.
Archives, payloads and reconstructed libraries are verified before activation;
the cache is not a permanent runtime library path. See [installation and recovery](meta/README.md).

Every compressed `.tgz` has a **350 MiB maximum**, enforced after `npm pack`,
during collection, and again before publication/promotion. This leaves room
below the approximately 384 MiB tarball ceiling caused by npm's base64/JSON
publication path and 64-bit Node's string limit. `npm pack` succeeding alone is
not publication validation. A component that outgrows the budget fails closed;
it must be split further, not silently published as a reduced CPU package.

`LICENSES/` and `SBOM.spdx.json` require reviewed per-target compliance inputs;
validation candidates may omit them, but publication and promotion may not.

`bin/` contains only the native executable. All runtime libraries, providers,
plugins, and dictionaries live under `lib/`. Unix binaries use a `../lib`
rpath. Windows uses a native MSVC delay-load hook to resolve DLLs from the
executable's sibling `lib/`. The binary runs directly, without a JavaScript
launcher or custom loader environment variables for bundled libraries. Host
NVIDIA libraries must still be discoverable by the system loader.

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
the release matrix.

The CUDA 13.1 primary GGUF plugin uses
`75-real;80-real;86-real;89-real;90-real;90-virtual`. The CUDA 12.4 fallback uses
`50-virtual;61-virtual;70-virtual;75-virtual;80-virtual;86-real`: PTX plus native
SM86 code, reducing installed size while retaining driver JIT compilation where
compatible native code is unavailable. `CMAKE_CUDA_ARCHITECTURES` overrides the
primary build; `CUDA12_ARCHITECTURES` overrides only the fallback, which does not
inherit the primary list. These build targets do not imply hardware validation
on every GPU generation. The current runtime device-enumeration check is not
a PTX/driver compatibility check: this release does not add PTX-version rejection
or subprocess crash isolation, and does not claim support for every older
CUDA 12 driver. CPU/GPU CI validates its specific driver/runtime environments.

The primary GGUF CUDA plugin remains directly under `lib/`; there is no new
`lib/cuda13/` directory. Explicit CPU execution uses a selective CPU-plugin
loader rather than scanning that directory for every backend, and ignores
`GGML_BACKEND_PATH`. Optional GPU registration is separate from CPU initialization.

On Windows, CUDA 13 runtime DLLs reside under the toolkit's `bin/x64`, while
older toolkits use `bin`. The required host runtime directories must be visible
to the loader (for example, through PATH); the package's sibling `lib/` lookup
does not install or locate missing NVIDIA runtime dependencies automatically.

The meta package locks every platform package to its exact version through
`optionalDependencies`. CUDA-capable bases use schema-3 base checksums plus a
hash-bound `plugins.json` with exact versions, core identity, archive integrity,
payload hashes and final reconstructed-library hashes. Plugin tarballs are packed
first so the base pins their actual bytes. `variant`/`accelerators` describe
compiled support; `bundledAccelerators` and `native-install.json` distinguish
the CPU base from the installed plugin set. The core remains dynamically
extensible: it is not replaced with a `--cpu-only` build. The npm command is always named `zg`; the meta
package retains the cross-platform `bin/zg.exe` native entry point so npm's
Windows shim and Unix symlink both enter the executable directly.

The published meta package supports Node.js 14.14 and newer for installation
only, not for CLI execution. Online plugin transport requires npm 7+ and
extraction uses system `tar`; local archives need no network. The root workspace may require a newer Node version for
development and package tests without reducing published compatibility.

Release order:

1. Build and package every native target; apply ad hoc signatures on macOS.
2. Collect the exact-version meta/native/component candidates once. Verify
   payload closure and checksums, run CPU and requested GPU checks in parallel,
   and require all publication gates including licenses and SBOMs to pass.
3. Publish runtime components before their base native packages to `rust-canary`, then publish the meta package
   to `rust-canary` last. Candidate publication never changes `latest`.
4. On every platform's ordinary CPU runner, install the exact meta-package version from the
   registry alone, so npm must select its native optional dependency.
5. Verify CLI startup, managed-rg, daemon startup and IPC, then update from a
   distinct published baseline version and roll back to that version. On the
   CUDA targets also force a registry plugin installation and verify its hashes
   and core identity; CPU fallback must not hide a plugin-fetch failure.
6. Promote native `latest` tags and then the meta `latest` tag in a separate job,
   only after all five registry install/update/rollback jobs pass.

`.github/workflows/rust-publish.yml` builds and packs the selected targets once on
their native runners in parallel, collects the candidates into a downloadable
artifact, then checks real installation, CLI startup, managed-rg and daemon IPC. The manual
`platform` input defaults to `all`; each of the five target names can be selected
individually.
`publish=true` requires `platform=all`; a subset is a validation-only candidate,
not a complete release. Pushes to `main` and
`release/rust-*`, and manual runs with `publish=false, cuda=false`, use ordinary CPU runners;
they do not allocate GPUs or publish packages. CUDA compilation still runs
for the default Linux/Windows x64 bundles and does not require GPU hardware.
All packaging builds stay on ordinary CPU runners, including when `cuda=true`
or `publish=true`. Manual `cuda=true` runs add separate Linux/Windows x64 GPU
jobs that download the exact native npm artifacts from the same workflow run
and perform package-level GPU end-to-end validation, without rebuilding or
repacking. Publishing also enables GPU validation unconditionally, even if
`cuda` was not selected. Candidate collection precedes parallel CPU/GPU checks;
it proves package completeness, not runtime success. A GPU failure does not skip
CPU checks or remove the candidate, but blocks npm publication.
Runs use branch-plus-platform concurrency groups, allowing different platform
selections on the same branch to run concurrently without cancelling an
in-progress run in the same group.
Intel macOS no longer gates the other platform builds. Its ONNX Runtime with
CoreML and zvec SDK are built from
pinned sources, cached, and bundled with the native package. The Intel release
targets macOS 14 and later.

Build jobs select Cargo/CMake parallelism from the runner's available CPUs and
total memory, budgeting 3 GiB per job and capping concurrency at 8. Both Linux
CUDA containers receive the same limits. This is a conservative memory heuristic,
not an OOM guarantee. Linux x64 and Windows x64 have a 180-minute timeout for the
primary CUDA 13.1 build plus CUDA 12.4 fallback. Windows installs MSVC 14.38 before
any compilation, then switches to that toolset only for the fallback build.

All-platform runs produce `rust-release-<version>`; subset runs produce
`rust-candidate-<version>` with the selected native tarballs and all their components, the same
exact-version meta tarball, checksums and a `release.json` marked `partial`.
Publication and promotion reject partial candidates. After collection,
independent CPU jobs for every selected target download and install the exact
base/meta tarballs offline without installing any CUDA components, rebuilding or repacking. They verify
checksums, CLI startup and daemon IPC. Linux/Windows x64 also verify real
GGUF/ONNX indexing and vector queries with
explicit CPU selection and default CPU fallback, without CUDA host libraries.
These checks also run for unpublished candidates and must pass before npm
publication; failures retain the candidate and `cpu-artifact-evidence-*` logs.

The package GPU jobs use the hosted T4 runners with CUDA runtime libraries,
not Rust/C++/CUDA compiler setup. They verify the downloaded native package's
payload checksums, share the `install-artifacts` installation path with CPU smoke
checks (real meta-package postinstall, no synthetic metadata). Installation runs
inside each Linux CUDA container and after Windows runtime preparation, using
fresh prefixes/caches and only this run's artifacts. Auto installation is tested
in CUDA 12 and forced installation in CUDA 13, with explicit CPU installation on
a GPU host also covered. Both CUDA 12 fallback and CUDA 13 primary paths require
real inference evidence, not merely successful installation.
Both verification paths use the current platform's native artifact plus the
small `rust-verification-<version>` meta/inventory artifact; their installation
reports identify the exact collected hashes. Registry checks run on CPU runners
and validate downloaded component versions/hashes and the reconstructed runtime,
so CPU fallback cannot mask missing or corrupt GPU payloads.
Windows GPU jobs also prepare the pinned NVIDIA 591.59 driver when an older
driver is active, verify the installer signature and CUDA initialization, and fail on a
reboot requirement instead of rebooting. Both Rust CI and package GPU validation
configure the CUDA DLL search paths and check plugin/dependency loading before
inference. These steps prepare temporary CI runners, not end-user npm installs;
see [Windows GPU runner preparation](../CONTRIBUTING.md#windows-gpu-runner-preparation).
`rust-ci.yml` separately verifies the workspace and all
three real models using each platform's already-compiled test binary. Its manual
`cuda_verify` input defaults to `false`. When enabled, Linux/Windows test jobs
use GPU runners and compile with `zg/cuda` from the outset, then reuse those
artifacts for the normal tests and additional ORT/llama.cpp CUDA verification.
Model2Vec remains CPU-only and macOS remains non-CUDA. The ordinary quality job
stays separate. `platform=linux` or `windows` limits test platforms; optional
`ref` selects the reviewed source commit. These CI checks do not package or publish.
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
authentication for `npm dist-tag`. Configure trusted publishing for all 13
packages with repository `zvec-ai/zvec-grep`, workflow filename
`rust-publish.yml`, and both **Allow npm publish** and **Allow npm dist-tag**.
The dist-tag permission is independent and disabled by default; `id-token:
write` alone does not grant it. See the [npm trusted publishing documentation](https://docs.npmjs.com/trusted-publishers/#managing-dist-tags-with-trusted-publishing).
An eligible `NPM_TOKEN` remains an alternative authentication method.

Developer certificate signing and Apple notarization can be added to the native
build steps independently of npm publishing.
