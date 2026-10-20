use super::*;

#[test]
fn cuda124_or_newer_needs_no_native_code_exception() {
    for version in [12040, 12080, 13010] {
        assert!(check_capability(version, false, true, || panic!("no device query")).is_ok());
    }
}

#[test]
fn old_cuda12_retains_compatible_native_code() {
    for version in [12000, 12020, 12030] {
        for devices in [vec![(8, 6)], vec![(8, 9)], vec![(8, 6), (8, 9)]] {
            assert!(check_capability(version, true, false, || Ok(devices)).is_ok());
        }
    }
}

#[test]
fn old_driver_rejects_ptx_only_and_mixed_devices() {
    for devices in [
        vec![(5, 0)],
        vec![(6, 1)],
        vec![(7, 0)],
        vec![(7, 5)],
        vec![(8, 0)],
        vec![(9, 0)],
        vec![(8, 6), (7, 5)],
        vec![(7, 5), (8, 6)],
        vec![],
    ] {
        assert!(check_capability(12030, true, false, || Ok(devices)).is_err());
    }
}

#[test]
fn unknown_or_pre_cuda12_driver_cannot_use_the_exception() {
    for version in [0, -1, 11080] {
        assert!(check_capability(version, true, false, || panic!("no query")).is_err());
    }
}

#[test]
fn forced_ptx_and_custom_plugins_cannot_use_the_native_exception() {
    for (native_sm86, force_ptx) in [(false, false), (true, true)] {
        assert!(check_capability(12030, native_sm86, force_ptx, || panic!("no query")).is_err());
    }
}

#[test]
fn forced_ptx_environment_obeys_cuda_precedence() {
    let yes = Some(OsStr::new("1"));
    let no = Some(OsStr::new("0"));
    assert!(!force_ptx_jit(None, None));
    assert!(force_ptx_jit(yes, None));
    assert!(force_ptx_jit(None, yes));
    assert!(force_ptx_jit(yes, no));
    assert!(!force_ptx_jit(no, yes));
    assert!(force_ptx_jit(Some(OsStr::new("01")), None));
    assert!(force_ptx_jit(Some(OsStr::new("unknown")), None));
}

#[test]
fn failed_device_query_is_not_treated_as_native_compatibility() {
    assert_eq!(
        check_capability(12030, true, false, || Err("probe failed".to_owned())),
        Err("probe failed".to_owned())
    );
    assert!(succeeded("cuInit", 0).is_ok());
    assert_eq!(
        succeeded("cuInit", 100),
        Err("cuInit failed with CUDA error 100".to_owned())
    );
}
