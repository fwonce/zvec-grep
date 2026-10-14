use super::*;

#[test]
fn explicit_library_has_priority_over_bundle_and_executable() {
    let path = PathBuf::from("custom/runtime");
    assert_eq!(
        runtime_path(
            Some(path.clone()),
            Some("bundle".into()),
            Some("bin/zg".into())
        )
        .expect("explicit runtime"),
        path
    );
}

#[test]
fn uses_bundle_override_or_executable_directory_without_cwd_lookup() {
    let override_path =
        runtime_path(None, Some("bundle".into()), Some("bin/zg".into())).expect("bundle runtime");
    assert_eq!(override_path.parent(), Some(std::path::Path::new("bundle")));
    let adjacent = runtime_path(None, None, Some("bin/zg".into())).expect("adjacent runtime");
    assert_eq!(adjacent.parent(), Some(std::path::Path::new("bin")));
    assert_eq!(adjacent.file_name(), override_path.file_name());
}

#[test]
fn unknown_executable_requires_an_explicit_runtime_location() {
    assert!(runtime_path(None, None, None).is_err());
    assert!(runtime_path(None, Some("bundle".into()), None).is_ok());
}
