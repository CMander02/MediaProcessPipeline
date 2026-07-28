use std::{
    env, fs,
    io::Read,
    path::{Path, PathBuf},
    process::Command,
};

use sha2::{Digest, Sha256};

const DEVELOPMENT_MANIFEST_SENTINEL: &str = "development-no-runtime-manifest";
const BUILD_INPUT_DIGEST_DOMAIN: &[u8] = b"mpp-build-input-v1\0";
const FORBIDDEN_GIT_ENVIRONMENT: &[&str] = &[
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_ATTR_NOSYSTEM",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_EXEC_PATH",
    "GIT_GLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
    "GIT_INDEX_FILE",
    "GIT_LITERAL_PATHSPECS",
    "GIT_NAMESPACE",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
];
const WATCHED_GIT_CONFIG_ENVIRONMENT: &[&str] = &[
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
];

fn valid_commit(value: &str) -> bool {
    value.len() == 40 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn environment_commit(name: &str) -> Result<Option<String>, String> {
    match env::var(name) {
        Ok(value) => {
            let commit = value.trim().to_ascii_lowercase();
            if !valid_commit(&commit) {
                return Err(format!(
                    "{name} must be a full 40-character hexadecimal Git SHA; received {value:?}"
                ));
            }
            Ok(Some(commit))
        }
        Err(env::VarError::NotPresent) => Ok(None),
        Err(env::VarError::NotUnicode(_)) => Err(format!("{name} is not valid Unicode")),
    }
}

fn reject_git_environment_overrides() -> Result<(), String> {
    let mut configured = Vec::new();
    for name in FORBIDDEN_GIT_ENVIRONMENT {
        if env::var_os(name).is_some() {
            configured.push(*name);
        }
    }
    for (name, _) in env::vars_os() {
        if name.to_string_lossy().starts_with("GIT_CONFIG_") {
            configured.push("GIT_CONFIG_*");
            break;
        }
    }
    configured.sort_unstable();
    configured.dedup();
    if configured.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "release source identity cannot be resolved with Git repository overrides: {}",
            configured.join(", ")
        ))
    }
}

fn git_output(project_root: &Path, arguments: &[&str]) -> Result<String, String> {
    let output = git_raw_output(project_root, arguments)?;
    String::from_utf8(output).map_err(|error| {
        format!(
            "git {} returned non-UTF-8 output: {error}",
            arguments.join(" ")
        )
    })
}

fn git_raw_output(project_root: &Path, arguments: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("git")
        .args(arguments)
        .current_dir(project_root)
        .output()
        .map_err(|error| format!("failed to execute git {}: {error}", arguments.join(" ")))?;
    if !output.status.success() {
        return Err(format!(
            "git {} failed: {}",
            arguments.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(output.stdout)
}

fn git_commit(project_root: &Path) -> Result<String, String> {
    let commit = git_output(project_root, &["rev-parse", "HEAD"])?
        .trim()
        .to_ascii_lowercase();
    if !valid_commit(&commit) {
        return Err(format!(
            "git rev-parse HEAD returned an invalid commit: {commit:?}"
        ));
    }
    Ok(commit)
}

fn git_tree_identity(project_root: &Path, commit: &str) -> Result<(String, String), String> {
    let tree = git_output(project_root, &["rev-parse", "HEAD^{tree}"])?
        .trim()
        .to_ascii_lowercase();
    if !(tree.len() == 40 || tree.len() == 64) || !tree.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(format!("git returned an invalid HEAD tree: {tree:?}"));
    }
    let listing = git_raw_output(
        project_root,
        &["ls-tree", "-r", "-z", "--full-tree", "HEAD"],
    )?;
    let mut digest = Sha256::new();
    digest.update(BUILD_INPUT_DIGEST_DOMAIN);
    digest.update(commit.as_bytes());
    digest.update(b"\0");
    digest.update(tree.as_bytes());
    digest.update(b"\0");
    digest.update(listing);
    Ok((tree, format!("{:x}", digest.finalize())))
}

fn verify_repository_top_level(project_root: &Path) -> Result<(), String> {
    let reported =
        PathBuf::from(git_output(project_root, &["rev-parse", "--show-toplevel"])?.trim());
    let expected = project_root
        .canonicalize()
        .map_err(|error| format!("cannot resolve project root: {error}"))?;
    let actual = reported.canonicalize().map_err(|error| {
        format!(
            "cannot resolve Git top-level {}: {error}",
            reported.display()
        )
    })?;
    if actual != expected {
        return Err(format!(
            "Git top-level {} differs from project root {}",
            actual.display(),
            expected.display()
        ));
    }
    Ok(())
}

fn reject_git_index_flags(project_root: &Path) -> Result<(), String> {
    let listing = git_raw_output(project_root, &["ls-files", "-v", "-z"])?;
    let mut violations = Vec::new();
    for record in listing
        .split(|byte| *byte == 0)
        .filter(|record| !record.is_empty())
    {
        if record.len() < 3 || record[1] != b' ' {
            return Err("git ls-files returned an invalid index record".to_string());
        }
        let tag = record[0];
        let relative_path = String::from_utf8_lossy(&record[2..]);
        if tag == b'S' {
            violations.push(format!("skip-worktree: {relative_path}"));
        }
        if tag.is_ascii_lowercase() {
            violations.push(format!("assume-unchanged: {relative_path}"));
        }
    }
    if violations.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "release Git index flags are forbidden:\n{}",
            violations.join("\n")
        ))
    }
}

fn resolve_build_commit(
    project_root: &Path,
    repository_available: bool,
) -> Result<(String, Option<String>), String> {
    let explicit_commit = environment_commit("MPP_SOURCE_COMMIT")?;
    let github_commit = environment_commit("GITHUB_SHA")?;
    if let (Some(left), Some(right)) = (&explicit_commit, &github_commit) {
        if left != right {
            return Err(format!(
                "MPP_SOURCE_COMMIT {left} differs from GITHUB_SHA {right}"
            ));
        }
    }

    let repository_commit = if repository_available {
        Some(git_commit(project_root)?)
    } else {
        None
    };
    if let Some(head) = repository_commit.as_deref() {
        for (name, candidate) in [
            ("MPP_SOURCE_COMMIT", explicit_commit.as_deref()),
            ("GITHUB_SHA", github_commit.as_deref()),
        ] {
            if let Some(value) = candidate {
                if value != head {
                    return Err(format!(
                        "{name} {value} differs from repository HEAD {head}"
                    ));
                }
            }
        }
    }

    let build_commit = explicit_commit
        .or(github_commit)
        .or_else(|| repository_commit.clone())
        .ok_or_else(|| {
            "unable to determine the build commit: set MPP_SOURCE_COMMIT or build from a Git checkout"
                .to_string()
        })?;
    Ok((build_commit, repository_commit))
}

fn git_directory(project_root: &Path) -> Option<PathBuf> {
    let dot_git = project_root.join(".git");
    if dot_git.is_dir() {
        return Some(dot_git);
    }
    let marker = fs::read_to_string(&dot_git).ok()?;
    let value = marker.trim().strip_prefix("gitdir:")?.trim();
    let candidate = PathBuf::from(value);
    Some(if candidate.is_absolute() {
        candidate
    } else {
        project_root.join(candidate)
    })
}

fn emit_git_rerun_paths(project_root: &Path) {
    let Some(git_dir) = git_directory(project_root) else {
        return;
    };
    let common_dir = fs::read_to_string(git_dir.join("commondir"))
        .ok()
        .map(|value| {
            let candidate = PathBuf::from(value.trim());
            if candidate.is_absolute() {
                candidate
            } else {
                git_dir.join(candidate)
            }
        })
        .unwrap_or_else(|| git_dir.clone());
    let head = git_dir.join("HEAD");
    println!("cargo:rerun-if-changed={}", head.display());
    println!("cargo:rerun-if-changed={}", git_dir.join("index").display());
    println!(
        "cargo:rerun-if-changed={}",
        common_dir.join("packed-refs").display()
    );
    if let Ok(contents) = fs::read_to_string(&head) {
        if let Some(reference) = contents.trim().strip_prefix("ref:") {
            println!(
                "cargo:rerun-if-changed={}",
                common_dir.join(reference.trim()).display()
            );
        }
    }
}

fn emit_tracked_rerun_paths(project_root: &Path) -> Result<(), String> {
    let output = Command::new("git")
        .args(["ls-files", "-z"])
        .current_dir(project_root)
        .output()
        .map_err(|error| format!("failed to execute git ls-files: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "git ls-files failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let paths = String::from_utf8(output.stdout)
        .map_err(|error| format!("git ls-files returned non-UTF-8 paths: {error}"))?;
    for relative_path in paths.split('\0').filter(|value| !value.is_empty()) {
        if relative_path.contains('\r')
            || relative_path.contains('\n')
            || Path::new(relative_path).is_absolute()
        {
            return Err(format!(
                "git ls-files returned an unsafe tracked path: {relative_path:?}"
            ));
        }
        println!(
            "cargo:rerun-if-changed={}",
            project_root.join(relative_path).display()
        );
    }
    Ok(())
}

fn file_sha256(path: &Path) -> Result<String, String> {
    let mut file =
        fs::File::open(path).map_err(|error| format!("cannot open {}: {error}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 64 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn validate_release_manifest_identity(
    runtime_manifest: &Path,
    project_root: &Path,
    build_commit: &str,
    app_version: &str,
) -> Result<(), String> {
    let manifest_bytes = fs::read(runtime_manifest).map_err(|error| {
        format!(
            "cannot read staged runtime manifest {}: {error}",
            runtime_manifest.display()
        )
    })?;
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes).map_err(|error| {
        format!(
            "staged runtime manifest is not valid JSON ({}): {error}",
            runtime_manifest.display()
        )
    })?;
    let object = manifest
        .as_object()
        .ok_or_else(|| "staged runtime manifest must contain a JSON object".to_string())?;
    if object.get("schema").and_then(serde_json::Value::as_u64) != Some(1) {
        return Err("staged runtime manifest schema must be 1".to_string());
    }
    if object.get("appVersion").and_then(serde_json::Value::as_str) != Some(app_version) {
        return Err(format!(
            "staged runtime appVersion must equal Cargo package version {app_version}"
        ));
    }
    if object
        .get("sourceCommit")
        .and_then(serde_json::Value::as_str)
        != Some(build_commit)
    {
        return Err(format!(
            "staged runtime sourceCommit must equal attested build commit {build_commit}"
        ));
    }
    if object
        .get("sourceDirty")
        .and_then(serde_json::Value::as_bool)
        != Some(false)
    {
        return Err("staged runtime sourceDirty must be false for release builds".to_string());
    }
    if object
        .get("toolContract")
        .and_then(serde_json::Value::as_str)
        != Some("packaging/desktop-tools.json")
    {
        return Err("staged runtime toolContract must be packaging/desktop-tools.json".to_string());
    }
    if !object.get("files").is_some_and(serde_json::Value::is_array) {
        return Err("staged runtime files must be an array".to_string());
    }
    if !object.get("uv").is_some_and(serde_json::Value::is_object) {
        return Err("staged runtime uv contract must be an object".to_string());
    }

    let staged_contract = runtime_manifest
        .parent()
        .expect("runtime manifest must have a parent")
        .join("packaging")
        .join("desktop-tools.json");
    let trusted_contract = project_root.join("packaging").join("desktop-tools.json");
    let staged_bytes = fs::read(&staged_contract).map_err(|error| {
        format!(
            "cannot read staged desktop tool contract {}: {error}",
            staged_contract.display()
        )
    })?;
    let trusted_bytes = fs::read(&trusted_contract).map_err(|error| {
        format!(
            "cannot read trusted desktop tool contract {}: {error}",
            trusted_contract.display()
        )
    })?;
    if staged_bytes != trusted_bytes {
        return Err(
            "staged desktop tool contract differs byte-for-byte from the trusted source"
                .to_string(),
        );
    }
    Ok(())
}

fn bounded_output(bytes: &[u8]) -> String {
    const MAX_OUTPUT_BYTES: usize = 16 * 1024;
    let start = bytes.len().saturating_sub(MAX_OUTPUT_BYTES);
    String::from_utf8_lossy(&bytes[start..]).trim().to_string()
}

fn run_release_runtime_verifier(
    project_root: &Path,
    runtime_resources: &Path,
    build_commit: &str,
    app_version: &str,
    build_input_digest: &str,
) -> Result<(), String> {
    let verifier = project_root
        .join("scripts")
        .join("check-desktop-runtime.py");
    let tool_contract = project_root.join("packaging").join("desktop-tools.json");
    if !verifier.is_file() {
        return Err(format!(
            "desktop runtime verifier is missing: {}",
            verifier.display()
        ));
    }
    if !tool_contract.is_file() {
        return Err(format!(
            "trusted desktop tool contract is missing: {}",
            tool_contract.display()
        ));
    }

    let explicit_python = env::var_os("MPP_BUILD_PYTHON").filter(|value| !value.is_empty());
    let candidates = if let Some(program) = explicit_python {
        vec![program]
    } else if cfg!(windows) {
        vec!["python".into()]
    } else {
        vec!["python3".into(), "python".into()]
    };
    let mut launch_errors = Vec::new();
    for program in candidates {
        let result = Command::new(&program)
            .args(["-I", "-S", "-B"])
            .arg(&verifier)
            .arg(runtime_resources)
            .args(["--expected-source-commit", build_commit])
            .args(["--expected-app-version", app_version])
            .args(["--expected-build-input-digest", build_input_digest])
            .arg("--require-clean-source")
            .arg("--tool-contract")
            .arg(&tool_contract)
            .current_dir(project_root)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("PYTHONUTF8", "1")
            .output();
        match result {
            Ok(output) if output.status.success() => return Ok(()),
            Ok(output) => {
                let stdout = bounded_output(&output.stdout);
                let stderr = bounded_output(&output.stderr);
                return Err(format!(
                    "desktop runtime release attestation failed with {}.\nstdout:\n{}\nstderr:\n{}",
                    output.status, stdout, stderr
                ));
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                launch_errors.push(format!("{}: {error}", Path::new(&program).display()));
            }
            Err(error) => {
                return Err(format!(
                    "could not launch desktop runtime verifier with {}: {error}",
                    Path::new(&program).display()
                ));
            }
        }
    }
    Err(format!(
        "no Python interpreter could run the release attestation; set MPP_BUILD_PYTHON to an \
         offline Python 3 interpreter ({})",
        launch_errors.join("; ")
    ))
}

fn main() {
    println!("cargo:rerun-if-env-changed=MPP_SOURCE_COMMIT");
    println!("cargo:rerun-if-env-changed=MPP_SOURCE_TREE");
    println!("cargo:rerun-if-env-changed=MPP_BUILD_INPUT_SHA256");
    println!("cargo:rerun-if-env-changed=GITHUB_SHA");
    println!("cargo:rerun-if-env-changed=MPP_BUILD_PYTHON");
    for name in FORBIDDEN_GIT_ENVIRONMENT
        .iter()
        .chain(WATCHED_GIT_CONFIG_ENVIRONMENT)
    {
        println!("cargo:rerun-if-env-changed={name}");
    }

    let manifest_dir =
        PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest directory"));
    let project_root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .expect("desktop manifest directory must be nested under the repository");
    let profile = env::var("PROFILE").unwrap_or_default();
    emit_git_rerun_paths(project_root);
    let repository_available = project_root.join(".git").exists();
    if repository_available {
        reject_git_environment_overrides()
            .unwrap_or_else(|error| panic!("cannot attest desktop repository: {error}"));
        verify_repository_top_level(project_root)
            .unwrap_or_else(|error| panic!("cannot attest desktop repository: {error}"));
        reject_git_index_flags(project_root)
            .unwrap_or_else(|error| panic!("cannot attest desktop repository: {error}"));
        emit_tracked_rerun_paths(project_root)
            .unwrap_or_else(|error| panic!("cannot register release source inputs: {error}"));
    }
    let (build_commit, repository_commit) =
        resolve_build_commit(project_root, repository_available)
            .unwrap_or_else(|error| panic!("cannot attest desktop build identity: {error}"));
    let (build_tree, build_input_digest) = if repository_available {
        git_tree_identity(project_root, &build_commit)
            .unwrap_or_else(|error| panic!("cannot attest desktop build inputs: {error}"))
    } else {
        let tree = env::var("MPP_SOURCE_TREE").unwrap_or_else(|_| {
            panic!("MPP_SOURCE_TREE is required when building outside a Git checkout")
        });
        let digest = env::var("MPP_BUILD_INPUT_SHA256").unwrap_or_else(|_| {
            panic!("MPP_BUILD_INPUT_SHA256 is required when building outside a Git checkout")
        });
        (tree, digest)
    };
    if let Ok(expected_tree) = env::var("MPP_SOURCE_TREE") {
        if expected_tree.trim().to_ascii_lowercase() != build_tree {
            panic!(
                "MPP_SOURCE_TREE {} differs from repository HEAD tree {build_tree}",
                expected_tree.trim()
            );
        }
    } else if profile == "release" {
        panic!("release builds require MPP_SOURCE_TREE from the formal desktop build entry");
    }
    if let Ok(expected_digest) = env::var("MPP_BUILD_INPUT_SHA256") {
        if expected_digest.trim().to_ascii_lowercase() != build_input_digest {
            panic!(
                "MPP_BUILD_INPUT_SHA256 {} differs from computed build input digest \
                 {build_input_digest}",
                expected_digest.trim()
            );
        }
    } else if profile == "release" {
        panic!("release builds require MPP_BUILD_INPUT_SHA256 from the formal desktop build entry");
    }
    println!("cargo:rustc-env=MPP_BUILD_COMMIT={build_commit}");
    println!("cargo:rustc-env=MPP_BUILD_TREE={build_tree}");
    println!("cargo:rustc-env=MPP_BUILD_INPUT_SHA256={build_input_digest}");

    let runtime_resources = manifest_dir.join("resources").join("runtime");
    let runtime_manifest = runtime_resources.join("runtime-manifest.json");
    let web_distribution = project_root.join("web").join("dist");
    println!("cargo:rerun-if-changed={}", runtime_resources.display());
    println!("cargo:rerun-if-changed={}", web_distribution.display());
    println!(
        "cargo:rerun-if-changed={}",
        runtime_manifest.as_path().display()
    );
    let mut attested_release_manifest_hash = None;
    if profile == "release" {
        let repository_commit = repository_commit.as_deref().unwrap_or_else(|| {
            panic!(
                "release desktop builds require a Git checkout so staged payloads can be bound \
                 to trusted source files"
            )
        });
        if repository_commit != build_commit {
            panic!(
                "release repository HEAD {repository_commit} differs from build commit \
                 {build_commit}"
            );
        }
        let dirty_status = git_output(
            project_root,
            &[
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
        )
        .unwrap_or_else(|error| panic!("cannot inspect release worktree: {error}"));
        if !dirty_status.trim().is_empty() {
            panic!(
                "release desktop builds require a clean worktree; Git reported:\n{}",
                dirty_status.trim()
            );
        }
        let app_version =
            env::var("CARGO_PKG_VERSION").expect("Cargo must provide the application version");
        let initial_manifest_hash = file_sha256(&runtime_manifest).unwrap_or_else(|error| {
            panic!("cannot hash the release runtime manifest before attestation: {error}")
        });
        validate_release_manifest_identity(
            &runtime_manifest,
            project_root,
            &build_commit,
            &app_version,
        )
        .unwrap_or_else(|error| panic!("release runtime manifest identity is invalid: {error}"));
        run_release_runtime_verifier(
            project_root,
            &runtime_resources,
            &build_commit,
            &app_version,
            &build_input_digest,
        )
        .unwrap_or_else(|error| panic!("{error}"));
        let final_commit = git_commit(project_root)
            .unwrap_or_else(|error| panic!("cannot re-check release repository HEAD: {error}"));
        reject_git_index_flags(project_root)
            .unwrap_or_else(|error| panic!("cannot re-check release Git index: {error}"));
        let (final_tree, final_input_digest) = git_tree_identity(project_root, &final_commit)
            .unwrap_or_else(|error| panic!("cannot re-check release build inputs: {error}"));
        let final_status = git_output(
            project_root,
            &[
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
        )
        .unwrap_or_else(|error| panic!("cannot re-check release worktree: {error}"));
        if final_commit != build_commit
            || final_tree != build_tree
            || final_input_digest != build_input_digest
            || !final_status.trim().is_empty()
        {
            panic!(
                "repository state changed during release attestation; expected clean \
                 {build_commit}/{build_tree}/{build_input_digest}, received \
                 {final_commit}/{final_tree}/{final_input_digest} with status:\n{}",
                final_status.trim()
            );
        }
        let final_manifest_hash = file_sha256(&runtime_manifest).unwrap_or_else(|error| {
            panic!("cannot re-hash the release runtime manifest after attestation: {error}")
        });
        if final_manifest_hash != initial_manifest_hash {
            panic!(
                "staged runtime manifest changed during release attestation: \
                 {initial_manifest_hash} != {final_manifest_hash}"
            );
        }
        attested_release_manifest_hash = Some(final_manifest_hash);
    }
    let runtime_manifest_hash = if let Some(attested_hash) = attested_release_manifest_hash {
        attested_hash
    } else if runtime_manifest.is_file() {
        file_sha256(&runtime_manifest).unwrap_or_else(|error| {
            panic!("failed to hash the staged desktop runtime manifest: {error}")
        })
    } else if profile == "release" {
        panic!(
            "release desktop build requires a staged runtime manifest at {}; \
             run the Tauri release build command",
            runtime_manifest.display()
        );
    } else {
        println!(
            "cargo:warning=desktop runtime manifest is not staged; embedding the development-only \
             manifest sentinel"
        );
        DEVELOPMENT_MANIFEST_SENTINEL.to_string()
    };
    println!("cargo:rustc-env=MPP_RUNTIME_MANIFEST_SHA256={runtime_manifest_hash}");

    if !runtime_resources.is_dir() {
        panic!(
            "desktop runtime resource directory is missing at {}; restore the tracked \
             resources/runtime placeholder or run the Tauri build command",
            runtime_resources.display()
        );
    }
    tauri_build::build();
}
