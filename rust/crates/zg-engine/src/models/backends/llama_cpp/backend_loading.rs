#[cfg(any(feature = "cuda", test))]
use std::path::Path;
use std::sync::OnceLock;

// Optional GPU registration must not be part of the one-time core result:
// an initial CPU request must leave later GPU requests possible, and missing
// GPU plugins must not poison the CPU backend. Callers coordinate registry
// access with native model/context initialization.
pub(super) struct BackendInitialization<T> {
    core: OnceLock<Result<T, String>>,
    gpu: OnceLock<()>,
}

impl<T> BackendInitialization<T> {
    pub(super) const fn new() -> Self {
        Self {
            core: OnceLock::new(),
            gpu: OnceLock::new(),
        }
    }

    pub(super) fn initialize(
        &self,
        wants_gpu: bool,
        initialize_core: impl FnOnce() -> Result<T, String>,
        register_gpu: impl FnOnce(&T),
    ) -> Result<&T, &String> {
        let core = self.core.get_or_init(initialize_core).as_ref()?;
        if wants_gpu {
            self.gpu.get_or_init(|| register_gpu(core));
        }
        Ok(core)
    }
}

#[cfg(feature = "cuda")]
pub(super) fn load(directory: &Path) {
    let custom_plugin = std::env::var_os("GGML_BACKEND_PATH").is_some_and(|path| !path.is_empty());
    let selected = load_with_fallback(
        directory,
        custom_plugin,
        |native_sm86| {
            // Source builds can customize fallback targets. Only the known
            // distribution contract may claim the SM86 cubin exception.
            let distributed = distribution_fallback(
                option_env!("CUDA12_ARCHITECTURES"),
                option_env!("LLAMA_CPP_RUNTIME_DIR"),
            );
            zg_llama_native::check_cuda12_driver(native_sm86 && distributed)
        },
        |path| {
            llama_cpp_2::llama_backend::load_backends_from_path(path);
            llama_cpp_2::list_llama_ggml_backend_devices()
                .iter()
                .any(|device| device.backend.eq_ignore_ascii_case("CUDA"))
        },
    );
    if let Some(cuda_version) = selected {
        tracing::debug!(cuda_version, "llama.cpp CUDA backend library selected");
    }
}

#[cfg(any(feature = "cuda", test))]
fn distribution_fallback(architectures: Option<&str>, external_sdk: Option<&str>) -> bool {
    external_sdk.is_none()
        && architectures.is_none_or(|value| {
            value == "50-virtual;61-virtual;70-virtual;75-virtual;80-virtual;86-real"
        })
}

// Both plugins are built from the same locked llama.cpp source. Register only
// one usable CUDA implementation; a loadable DLL with zero devices is not usable.
#[cfg(any(feature = "cuda", test))]
fn load_with_fallback(
    directory: &Path,
    custom_plugin: bool,
    mut check_driver: impl FnMut(bool) -> Result<(), String>,
    mut load: impl FnMut(&Path) -> bool,
) -> Option<&'static str> {
    // The upstream directory loader also opens GGML_BACKEND_PATH. Check before
    // even the primary call so an override pointing at cuda12 cannot bypass the
    // guard. Unknown custom architecture lists never get the native-code waiver.
    if custom_plugin && let Err(reason) = check_driver(false) {
        tracing::warn!("Skipping GGUF GPU plugins: {reason}; falling back to CPU.");
        return None;
    }
    if load(directory) {
        return Some("13.1");
    }
    let fallback = directory.join("cuda12");
    if fallback.is_dir() {
        if let Err(reason) = check_driver(true) {
            tracing::warn!("Skipping GGUF CUDA 12.4 fallback: {reason}; falling back to CPU.");
        } else if load(&fallback) {
            return Some("12.4");
        }
    }
    None
}

#[cfg(test)]
#[path = "backend_loading_tests.rs"]
mod tests;
