# Rust rewrite contributor guide

The Rust workspace lives in `rust/`. Run the commands in this guide from that
directory; the repository root contains the TypeScript / Node.js implementation.

## Start

From the repository root:

```sh
cd rust
bash scripts/check.sh
cargo run -p zg -- --rg needle .
```

The workspace pins its Rust toolchain in `rust-toolchain.toml`. Lexical search
uses embedded `grep` and `ignore` crates, so a system `rg` executable is not
required for builds or tests.

## Engine changes

Keep the application surface centered on `ZvecGrep`:

- add a typed method or extend its request/reply types;
- implement behavior in a private service module;
- call that service directly from `ZvecGrep`;
- return `EngineError` without wrapping the result in a generic command outcome.

Do not add a generic `Core`, command bus, operation envelope, adapter registry or
transport executor to connect an in-process method to its implementation.

`zg_engine::authorization` is an explicit exception for remote-consent preflight
and signed grant-file management. CLI, daemon and MCP callers must be able to
resolve destinations and inspect, grant or revoke consent before starting an
engine operation. These functions use workspace metadata and authorization files;
they do not require a `ZvecGrep` instance, acquire model runtimes, open index
storage or send remote requests. Keep their implementation helpers private and
return typed data for new callers; terminal rendering belongs in `zg-cli`.
The existing string-returning helpers remain for compatibility. This exception
does not extend to indexing, search or other engine operations, which continue
to use typed `ZvecGrep` methods backed by private services.

## Native and transport changes

Native dependency types remain in their owning crate. Daemon framing and wire
commands remain in `zg-daemon-protocol`; MCP schemas remain in
`zg-transport-mcp`. Transport handlers call public `ZvecGrep` methods directly.

If a private engine service needs code currently living in another crate,
prefer moving that concrete implementation behind the engine boundary. Do not
make an engine-internal module public solely to avoid a crate dependency cycle.

## Compatibility

The TypeScript implementation in the repository root is the behavioral oracle
during the rewrite. Store stable, machine-readable cases under `compat/` and normalize
paths, random identifiers and timings in the runner.

## Verification

Run:

```sh
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
RUSTDOCFLAGS="-D warnings" cargo doc -p zg-engine --no-deps
```

## CUDA validation

CUDA-specific helpers live only in `scripts/cuda/`: `build.sh`/`build.ps1`
assemble the primary bundle, `build-fallback.py` adds only the CUDA 12.4 plugin,
`prepare-onnxruntime.py` verifies and stages official ORT archives, and
`check.sh`/`check.ps1` audit the resulting runtime linkage. `prepare-windows.ps1`
and the two pinned requirements files provision Windows build/runtime tools.
`cuda-builder.Dockerfile` provides the shared Linux CUDA build environment for
source CI and package builds.
`e2e.py` validates installed packages on a real GPU or, with `--cpu-only`, on a
host without loadable CUDA libraries. CUDA validation consists of actual
compilation, linkage audits and GPU end-to-end execution; ordinary PR CI retains
the npm packaging tests without a separate CUDA mock-test suite.

### Workspace and real-model CI

`Rust CI` (`.github/workflows/rust-ci.yml`) is the single entry point for
workspace and real-model verification. Push/PR runs use ordinary Linux, macOS
and Windows runners. The independent non-CUDA quality job runs formatting,
per-package checks, Clippy and npm packaging tests. Each test job compiles the
workspace tests once, runs them with pinned cargo-nextest 0.9.148 and checks the
CLI. It reuses the compiled `zg-engine` test binary for all three real models
without a separate Cargo build per model.

For manual `workflow_dispatch`, set `cuda_verify=true` to move the selected
Linux/Windows test jobs to `gpu-linux-t4` and `gpu-windows-t4` in `gpu-runners`.
`platform` accepts `all` (the default), `linux`, or `windows`. Optional `ref`
selects the reviewed source; otherwise the workflow's selected commit is used.
The source job resolves it once to a SHA for all subsequent checkouts.
CUDA test jobs enable `zg/cuda` from the first compilation and target T4 (SM 75).
The quality job remains non-CUDA, and macOS stays non-CUDA when `platform=all`.
This workflow does not call `rust-package-build.yml` or build/install npm packages;
it is not a substitute for package-level GPU validation.

The model stage runs the existing ignored real-model test for Model2Vec
(`local/potion-code-16m-v2`), Transformers/ORT (`local/all-minilm-l6-v2`) and
llama.cpp (`local/embeddinggemma-300m`). CUDA jobs additionally run ORT and GGUF
with `ZVEC_GREP_SMOKE_DEVICE=cuda`, sequentially within each platform job;
Model2Vec remains CPU-only. Linux uses separate CUDA 12/cuDNN and CUDA 13 runtime
containers for these GPU cases, with the same compiled test executable. Windows
installs both runtime families before compiling the CUDA-enabled tests.
The test downloads, loads and embeds with each model and checks vector dimensions
and finite/nonzero output. CUDA jobs also reuse the CLI validator to require CUDA
graph placement or positive GGUF layer offload without CPU fallback. Logs are
retained as `real-model-evidence-<platform>-<cuda|default>`, including on failure.
Any failed model case or CUDA evidence assertion fails the platform job.

Both GPU jobs then explicitly run the ignored C5 tests from those same compiled
test binaries, each in a fresh process: `cached_embeddinggemma_uses_cuda` verifies
CPU inference followed by CUDA inference in one process, while
`real_cpu_plugin_ignores_gpu_override` verifies that a real CPU plugin remains
the only registered backend even with `GGML_BACKEND_PATH` pointing at a present
CUDA plugin. They reuse the model cache and Cargo-reported backend directory;
no extra build is performed. Each selection must report exactly one passing
test, and `c5-*.log` files are retained with the other real-model evidence.

### Package validation

`Publish Rust packages` (`.github/workflows/rust-publish.yml`) handles
package-level validation. Pushes to `main` and
`release/rust-*`, and manual runs with both `publish=false` and `cuda=false`,
build/audit/package on ordinary CPU runners without allocating GPUs or publishing
to npm. Compilation always stays on CPU runners, including when `cuda=true` or
`publish=true`; Linux/Windows x64 still build the default CUDA-capable bundles.
Set `cuda=true,publish=false` to add GPU validation without publishing.
Separate GPU jobs download the exact native npm artifacts from the same run and
invoke `.github/actions/cuda-cli-e2e`, without rebuilding or repacking. Publication
always requires these GPU checks, even when `cuda` is unchecked. Collection runs
first; CPU and requested GPU validation then run independently in parallel.
Failures retain candidates and do not skip the other validation path, but every
required check must pass before publication. Collection alone is not approval.

The manual `platform` input selects `all` (the default) or one of `darwin-arm64`,
`darwin-x64`, `linux-x64-gnu`, `linux-arm64-gnu`, and `win32-x64-msvc`.
Selected platforms compile in parallel; Intel macOS does not gate the others.
All-platform runs collect `rust-release-<version>`; a single-platform run collects
`rust-candidate-<version>` with `partial: true` in `release.json`. The latter is
validation-only and cannot be published or promoted; `publish=true` requires
`platform=all`. Each collected artifact includes the selected native tarballs,
the exact-version meta tarball, checksums and installation instructions.

After collection, separate CPU jobs for every selected target download their
native artifact plus the collected meta/inventory artifact, verify tarball SHA256
values, and install only base/meta tarballs offline into a fresh prefix without
rebuilding or repacking. They verify CLI and daemon IPC. Linux/Windows x64 then
run `scripts/cuda/e2e.py --cpu-only` against the installed meta
package. This checks both explicit `--device cpu` and default automatic CPU
fallback for real GGUF/ONNX indexing and vector queries. Linux uses a plain
Ubuntu 24.04 container; Windows excludes toolkit paths. Both require CUDA host
libraries to be unavailable and retain `cpu-artifact-evidence-*` logs on failure.
CPU artifact validation runs even when `publish=false`; failure blocks npm
publication, while the downloaded candidate remains available for diagnosis.

CUDA component downloads are tested through the actual postinstall, not a
workspace-only assembly shortcut. Linux installs inside each CUDA runtime
container; Windows installs after the relevant runtime is prepared. Each case
uses a fresh prefix/cache and this run's local plugin archives. Auto and forced
CUDA installation must activate every verified plugin before GPU inference;
explicit CPU installation on a GPU host must request none. Registry smoke also
forces plugin fetching so optional CPU fallback cannot hide unavailable assets.

Both platforms run the same CLI index, readiness and vector-search test with
GGUF `local/embeddinggemma-300m` and ONNX `local/all-minilm-l6-v2`, explicitly
requesting CUDA with indexing concurrency 2. A successful command alone is not
a pass: index and search must select CUDA, GGUF must report positive layer offload
to the GPU, ONNX must place a nonzero number of
graph nodes on `CUDAExecutionProvider`, and any model-level CPU fallback fails
the test. ORT may still put supported shape/other nodes on CPU; this test does
not claim every ONNX operator runs on GPU.

Per-command stdout/stderr, GPU/driver details and a machine-readable result are
retained as `cuda-cli-evidence-*` artifacts, including when a model fails. Model
caches are reused. Package runs share a concurrency group only for the same
branch and platform selection; different platform selections can run concurrently.
An in-progress run is not cancelled by a newer run in its group. npm publication
still requires an explicit `publish=true` run and the release gates documented
in [npm distribution](npm/README.md).

To reproduce against an unpacked/installed CUDA bundle (Python 3.12 recommended):

```sh
python scripts/cuda/e2e.py --bundle /path/to/package --model-cache /path/to/models --logs /path/to/logs --expected-gguf-cuda 13.1
```

Package GPU jobs first run GGUF and ONNX with CUDA 12.8.1/cuDNN 9 visible,
requiring GGUF to select the plugin built with CUDA 12.4. Linux then runs GGUF in
a separate CUDA 13.1 runtime container. Windows hides any CUDA 13 cuBLAS DLL
directories from PATH for the fallback test, then installs CUDA 13.1 runtime
libraries and reruns both models, requiring the GGUF primary plugin and ONNX
CUDA 12 provider to coexist. Logs must prove the expected GGUF plugin and actual
GPU/provider selection. See [Native and CUDA builds](README.md#native-and-cuda-builds)
for the distinct primary and fallback distribution architecture lists.

### Windows GPU runner preparation

Both GPU workflows use `scripts/cuda/prepare-windows.ps1` on the ephemeral Windows
runner. It upgrades older NVIDIA drivers to the pinned Data Center 591.59 driver,
authenticates the installer signature, and verifies the active driver version,
CUDA Driver API initialization and device enumeration. A reboot requirement, an
inactive upgrade or failed CUDA initialization fails the job explicitly; the
helper never reboots the runner.

The Rust CI CUDA test job also provisions VS 2022 C++ tools (including MSVC 14.38),
a pinned libclang wheel and the repository-pinned Rust toolchain. The GPU image
may lack `pwsh`, so the helper downloads a SHA256-pinned portable
PowerShell 7.6.7 into `RUNNER_TEMP` for the nextest installer. A separate `pwsh`
step verifies shell lookup before installing nextest. Package GPU
jobs call the helper with `-DriverOnly` and install only runtime libraries, not
Rust/C++/CUDA build toolchains. This provisioning is not part of npm installation.
Both paths configure CUDA DLL lookup, including CUDA 13's `bin/x64` as well as
`bin`, and independently load the selected GGUF CUDA plugin and its dependencies
before real inference. DLL-load evidence is saved in `windows-cuda-load.json`
inside the corresponding model/package evidence artifact. Windows package E2E
uses a short `RUNNER_TEMP` workspace; it does not alter zg/zvec long-path handling.
Linux GPU runners must supply a compatible driver and NVIDIA container support.
CPU fallback after a driver/runtime failure does not count as successful GPU
validation.
