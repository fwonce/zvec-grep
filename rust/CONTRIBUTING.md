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

## Manual CUDA CLI validation

`Rust CUDA GPU CI` only runs through `workflow_dispatch`; PR updates and merges
do not allocate GPUs. Select a reviewed source ref and check `confirm_reviewed`.
The workflow resolves the ref once to a SHA, builds CUDA npm packages on ordinary
Linux/Windows runners, then installs those tarballs on the `gpu-runners` group's
`gpu-linux-t4` and `gpu-windows-t4` GitHub-hosted runners. Builds target T4 (SM 75)
for this test; release builds retain their full architecture list.

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
caches are reused, and only one GPU workflow runs at a time. Ordinary no-GPU CI
remains independent.

To reproduce against an unpacked/installed CUDA bundle (Python 3.12 recommended):

```sh
python scripts/test-cuda-cli.py --bundle /path/to/package/bin --model-cache /path/to/models --logs /path/to/logs --expected-gguf-cuda 13.1
python scripts/test-cuda-cli-checks.py  # assertion regression tests; no GPU needed
```

Both platforms first run GGUF and ONNX with only CUDA 12.8/cuDNN 9 installed,
requiring GGUF to select the CUDA 12.4 fallback. Linux then runs GGUF in a separate
CUDA 13.1 container; Windows adds CUDA 13.1 alongside CUDA 12 and reruns both models,
requiring the GGUF primary plugin and ONNX CUDA 12 provider to coexist. Logs must
prove the expected GGUF plugin as well as actual GPU/provider selection.
The Windows job can update its ephemeral VM's driver with Microsoft's NVIDIA-signed
591.59 installer. It never reboots; a reboot requirement fails with a clear error.
The Linux image must supply a compatible driver and NVIDIA container support.
Driver/runtime failures never count as successful CPU execution.
