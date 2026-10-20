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
    let selected = load_with_fallback(directory, |path| {
        llama_cpp_2::llama_backend::load_backends_from_path(path);
        llama_cpp_2::list_llama_ggml_backend_devices()
            .iter()
            .any(|device| device.backend.eq_ignore_ascii_case("CUDA"))
    });
    if let Some(cuda_version) = selected {
        tracing::debug!(cuda_version, "llama.cpp CUDA backend library selected");
    }
}

// Both plugins are built from the same locked llama.cpp source. Register only
// one usable CUDA implementation; a loadable DLL with zero devices is not usable.
#[cfg(any(feature = "cuda", test))]
fn load_with_fallback(
    directory: &Path,
    mut load: impl FnMut(&Path) -> bool,
) -> Option<&'static str> {
    if load(directory) {
        return Some("13.1");
    }
    let fallback = directory.join("cuda12");
    if fallback.is_dir() && load(&fallback) {
        return Some("12.4");
    }
    None
}

#[cfg(test)]
#[path = "backend_loading_tests.rs"]
mod tests;
