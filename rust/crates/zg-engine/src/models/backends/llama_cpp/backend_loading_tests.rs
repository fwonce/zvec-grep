use super::*;

#[test]
fn cpu_initialization_does_not_register_gpu_plugins() {
    let state = BackendInitialization::new();
    assert_eq!(
        state.initialize(false, || Ok(7), |_| panic!("CPU must not load GPU")),
        Ok(&7)
    );
    assert!(state.gpu.get().is_none());
}

#[test]
fn cpu_then_gpu_registers_plugins_without_reinitializing_core() {
    let state = BackendInitialization::new();
    state
        .initialize(false, || Ok(7), |_| panic!("CPU must not load GPU"))
        .expect("CPU");
    let mut registrations = 0;
    state
        .initialize(
            true,
            || panic!("core already initialized"),
            |core| {
                assert_eq!(*core, 7);
                registrations += 1;
            },
        )
        .expect("GPU");
    state
        .initialize(
            true,
            || panic!("core already initialized"),
            |_| panic!("GPU already registered"),
        )
        .expect("GPU again");
    assert_eq!(registrations, 1);
}

#[test]
fn missing_gpu_plugins_do_not_poison_the_cpu_core() {
    let state = BackendInitialization::new();
    state
        .initialize(true, || Ok(7), |_| { /* no GPU plugins found */ })
        .expect("core");
    assert_eq!(
        state.initialize(
            false,
            || panic!("core already initialized"),
            |_| panic!("CPU")
        ),
        Ok(&7)
    );
}

#[test]
fn failed_core_never_registers_gpu_plugins() {
    let state = BackendInitialization::<()>::new();
    assert!(
        state
            .initialize(
                true,
                || Err("CPU plugin unavailable".to_owned()),
                |()| panic!("no core")
            )
            .is_err()
    );
}

#[test]
fn concurrent_requests_initialize_core_and_gpu_once() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let state = BackendInitialization::new();
    let core_calls = AtomicUsize::new(0);
    let gpu_calls = AtomicUsize::new(0);
    std::thread::scope(|scope| {
        for wants_gpu in [false, true, false, true] {
            let state = &state;
            let core_calls = &core_calls;
            let gpu_calls = &gpu_calls;
            scope.spawn(move || {
                state
                    .initialize(
                        wants_gpu,
                        || {
                            core_calls.fetch_add(1, Ordering::SeqCst);
                            Ok(())
                        },
                        |()| {
                            gpu_calls.fetch_add(1, Ordering::SeqCst);
                        },
                    )
                    .expect("initialized");
            });
        }
    });
    assert_eq!(core_calls.load(Ordering::SeqCst), 1);
    assert_eq!(gpu_calls.load(Ordering::SeqCst), 1);
}

#[test]
fn working_primary_does_not_load_fallback() {
    let root = tempfile::tempdir().expect("temporary bundle");
    std::fs::create_dir(root.path().join("cuda12")).expect("fallback directory");
    let mut loaded = Vec::new();
    let selected = load_with_fallback(root.path(), |path| {
        loaded.push(path.to_path_buf());
        true
    });
    assert_eq!(selected, Some("13.1"));
    assert_eq!(loaded, [root.path()]);
}

#[test]
fn failed_primary_uses_cuda12() {
    let root = tempfile::tempdir().expect("temporary bundle");
    let fallback = root.path().join("cuda12");
    std::fs::create_dir(&fallback).expect("fallback directory");
    let mut loaded = Vec::new();
    let selected = load_with_fallback(root.path(), |path| {
        loaded.push(path.to_path_buf());
        path == fallback
    });
    assert_eq!(selected, Some("12.4"));
    assert_eq!(loaded, [root.path(), &fallback]);
}

#[test]
fn no_cuda_keeps_the_cpu_backend_loaded() {
    let root = tempfile::tempdir().expect("temporary bundle");
    std::fs::create_dir(root.path().join("cuda12")).expect("fallback directory");
    let mut attempts = 0;
    assert_eq!(
        load_with_fallback(root.path(), |_| {
            attempts += 1;
            false
        }),
        None
    );
    assert_eq!(attempts, 2);
}

#[test]
fn missing_fallback_is_not_loaded() {
    let root = tempfile::tempdir().expect("temporary bundle");
    let mut attempts = 0;
    assert_eq!(
        load_with_fallback(root.path(), |_| {
            attempts += 1;
            false
        }),
        None
    );
    assert_eq!(attempts, 1);
}
