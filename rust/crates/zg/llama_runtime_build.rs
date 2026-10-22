//! Deploy zg's load-time libraries, not a policy for other SDK consumers.
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fmt::Write as _,
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Deserialize)]
struct Manifest {
    schema: u32,
    target: String,
    linkage: String,
    link_libraries: Vec<String>,
    files: BTreeMap<String, String>,
}

fn sha256(path: &Path) -> io::Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 8192];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    let mut digest = String::with_capacity(64);
    for byte in hash.finalize() {
        write!(digest, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(digest)
}

fn runtime_name(filename: &str, library: &str, windows: bool) -> bool {
    if windows {
        filename == format!("{library}.dll")
    } else {
        let soname = format!("lib{library}.so");
        filename == soname || filename.starts_with(&format!("{soname}."))
    }
}

/// Consume only Cargo's direct-dependency SDK metadata. Never scan build caches
/// for a different ABI or copy GPU plugins into the load-time search directory.
///
/// # Errors
/// Returns an error for invalid SDK metadata, corrupt libraries or failed copies.
pub fn stage(root: &Path, manifest_path: &Path, target: &str, out: &Path) -> Result<Vec<PathBuf>> {
    let root = root.canonicalize()?;
    if manifest_path.canonicalize()?.parent() != Some(root.as_path()) {
        return Err("llama.cpp runtime manifest does not belong to its SDK directory".into());
    }
    let manifest: Manifest = serde_json::from_slice(&fs::read(manifest_path)?)?;
    if manifest.schema != 1 || manifest.target != target {
        return Err("llama.cpp runtime manifest schema/target mismatch".into());
    }
    if manifest.linkage == "static" {
        return Ok(Vec::new());
    }
    if manifest.linkage != "dynamic" || !(target.contains("linux") || target.contains("windows")) {
        return Err("unsupported llama.cpp shared runtime layout".into());
    }
    let profile = out.ancestors().nth(3).ok_or("invalid Cargo OUT_DIR")?;
    let libraries: Vec<_> = manifest
        .link_libraries
        .iter()
        .filter(|library| !library.starts_with("static="))
        .filter_map(|library| library.rsplit('=').next())
        .filter(|name| name.starts_with("llama") || name.starts_with("ggml"))
        .collect();
    let mut staged = Vec::new();
    for library in &libraries {
        let mut found = false;
        for (name, digest) in &manifest.files {
            let Some(filename) = name.strip_prefix("lib/") else {
                continue;
            };
            if filename.contains(['/', '\\', ':'])
                || !runtime_name(filename, library, target.contains("windows"))
            {
                continue;
            }
            found = true;
            let source = root.join(name);
            let source_metadata = fs::symlink_metadata(&source)?;
            if !source_metadata.is_file() || sha256(&source)? != *digest {
                return Err(format!("invalid llama.cpp runtime payload: {name}").into());
            }
            for directory in [
                profile.to_path_buf(),
                profile.join("deps"),
                profile.join("examples"),
            ] {
                fs::create_dir_all(&directory)?;
                let destination = directory.join(filename);
                let unchanged = fs::symlink_metadata(&destination)
                    .is_ok_and(|metadata| metadata.is_file())
                    && sha256(&destination)? == *digest;
                if !unchanged {
                    // Copy, never hard-link to the immutable SDK. Atomic replace
                    // also avoids truncating a library another zg is using.
                    let temporary = tempfile::NamedTempFile::new_in(&directory)?;
                    fs::copy(&source, temporary.path())?;
                    if sha256(temporary.path())? != *digest {
                        return Err(
                            format!("llama.cpp runtime changed while copying: {name}").into()
                        );
                    }
                    // Cargo watches these outputs to repair deleted libraries.
                    // Preserve the SDK mtime so writing them during this build
                    // does not force another link on the very next invocation.
                    temporary
                        .as_file()
                        .set_modified(source_metadata.modified()?)?;
                    temporary.persist(&destination)?;
                }
                staged.push(destination);
            }
        }
        if !found {
            return Err(
                format!("missing shared runtime for llama.cpp link library: {library}").into(),
            );
        }
    }
    if staged.is_empty() {
        return Err("llama.cpp dynamic SDK contains no load-time libraries".into());
    }
    Ok(staged)
}
