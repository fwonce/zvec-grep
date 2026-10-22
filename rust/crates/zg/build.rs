#[path = "llama_runtime_build.rs"]
mod llama_runtime;

fn main() {
    zvec_rust_build::configure();
    if std::env::var_os("CARGO_FEATURE_CUDA").is_some() {
        stage_ort_runtime();
        // Source builds already stage their shared libraries. Prebuilt SDKs
        // expose their verified runtime metadata; zg owns the executable layout.
        match (
            std::env::var_os("DEP_LLAMA_RUNTIME_DIR"),
            std::env::var_os("DEP_LLAMA_RUNTIME_MANIFEST"),
        ) {
            (Some(root), Some(manifest)) => {
                let target = std::env::var("TARGET").expect("Cargo TARGET");
                let out = std::env::var_os("OUT_DIR").expect("Cargo OUT_DIR");
                for path in llama_runtime::stage(
                    std::path::Path::new(&root),
                    std::path::Path::new(&manifest),
                    &target,
                    std::path::Path::new(&out),
                )
                .expect("stage zg's llama.cpp runtime libraries")
                {
                    println!("cargo:rerun-if-changed={}", path.display());
                }
            }
            (None, None) => {}
            _ => panic!("incomplete llama.cpp SDK runtime metadata"),
        }
    }
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        // Delay direct imports so the executable can locate its sibling lib/
        // before Windows resolves any native model/storage dependencies.
        cc::Build::new()
            .file("windows_runtime.c")
            .compile("zg_windows_runtime");
        println!("cargo:rerun-if-changed=windows_runtime.c");
        println!("cargo:rustc-link-lib=delayimp");
        println!("cargo:rustc-link-arg=/INCLUDE:__pfnDliNotifyHook2");
        println!("cargo:rustc-link-arg=/DELAYLOAD:zvec_c_api.dll");
        let libraries: &[&str] = if std::env::var_os("CARGO_FEATURE_CUDA").is_some() {
            &["llama.dll", "ggml.dll", "ggml-base.dll"]
        } else {
            &["onnxruntime.dll", "DirectML.dll"]
        };
        for library in libraries {
            println!("cargo:rustc-link-arg=/DELAYLOAD:{library}");
        }
    }
}

fn stage_ort_runtime() {
    let target = std::env::var("TARGET").expect("Cargo TARGET");
    let platform = match target.as_str() {
        "x86_64-unknown-linux-gnu" => "linux",
        "x86_64-pc-windows-msvc" => "win32",
        _ => panic!("zg's CUDA ONNX Runtime is unsupported on {target}"),
    };
    // Keep Cargo and npm on the same pinned distribution and extraction code.
    // ort/load-dynamic disables ort-sys's download-binaries/copy-dylibs path.
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scripts/cuda/prepare-onnxruntime.py");
    println!("cargo:rerun-if-changed={}", script.display());
    println!("cargo:rerun-if-env-changed=PYTHON");
    println!("cargo:rerun-if-env-changed=CARGO_NET_OFFLINE");
    let python = std::env::var_os("PYTHON")
        .unwrap_or_else(|| if cfg!(windows) { "python" } else { "python3" }.into());
    let status = std::process::Command::new(python)
        .arg(&script)
        .args(["--platform", platform, "--cargo-out-dir"])
        .arg(std::env::var_os("OUT_DIR").expect("Cargo OUT_DIR"))
        .status()
        .expect("staging zg's ONNX Runtime requires Python 3 (override with PYTHON)");
    assert!(
        status.success(),
        "staging zg's ONNX Runtime failed: {status}"
    );
}
