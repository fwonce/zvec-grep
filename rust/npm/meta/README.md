# zvec-grep

This is the npm distribution wrapper for the native `zg` CLI. The package uses
an exact-version optional dependency to install only the native package matching
the current operating system, CPU architecture, and Linux libc.

Linux/Windows x64 platform packages also require exact-version CUDA component
packages. These are selected by platform, not by GPU detection. The install
script verifies every component, rejoins Linux ORT fragments in a temporary
directory, checks the complete library hash, and materializes the
native `zg` executable under `bin/` and its runtime libraries under `lib/`.
No compilation, CUDA Toolkit installation or model execution happens during
installation. All components are needed even when the installed CLI runs on CPU.
The `zg` command enters the native executable directly. Unix rpaths and the
Windows native delay-load hook locate the sibling `lib/` without a launcher.

Node.js 14.14 or newer is required only for installation. CLI execution does
not require Node.js.

If installation scripts or optional dependencies are disabled, reinstall
without `--ignore-scripts` and without `--omit=optional`.
