#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    error::Error,
    ffi::{OsStr, OsString},
    fs,
    io::{self, Read, Write},
    net::{IpAddr, Ipv4Addr, Ipv6Addr, Shutdown, SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use chrono::Utc;
use cookie::{Cookie, SameSite};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use socket2::{Domain, Protocol, Socket, Type};
use tauri::{
    webview::NewWindowResponse, AppHandle, Emitter, Manager, RunEvent, WebviewUrl,
    WebviewWindowBuilder, WindowEvent,
};

#[cfg(test)]
const BACKEND_IPV4_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 18000;
const APP_URL: &str = "http://localhost:18000";
const DESKTOP_API_HOST: &str = "api.tauri.localhost";
const DESKTOP_PROXY_COOKIE: &str = "mpp_desktop_proxy";
const MAX_PROXY_CONNECTIONS: usize = 128;
const MAX_HTTP_HEAD_BYTES: usize = 64 * 1024;
const MAX_HEALTH_RESPONSE_BYTES: usize = 64 * 1024;
const MAX_CHUNK_LINE_BYTES: usize = 8 * 1024;
const MAX_TRAILER_BYTES: usize = 64 * 1024;
const PROXY_CLIENT_HEAD_TIMEOUT: Duration = Duration::from_secs(4);
const PROXY_CLIENT_BODY_IDLE_TIMEOUT: Duration = Duration::from_secs(30);
const PROXY_BACKEND_CONNECT_TIMEOUT: Duration = Duration::from_millis(500);
const PROXY_BACKEND_HEALTH_TIMEOUT: Duration = Duration::from_secs(2);
const BACKEND_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(8);
const BACKEND_COMMAND: &str = "uv run python -u -m app.cli serve --desktop-loopback";
const MAX_LOG_LINES: usize = 1200;
const MAX_LOG_LINE_BYTES: usize = 16 * 1024;
const PROCESS_FORCE_TIMEOUT: Duration = Duration::from_secs(3);
const PREFLIGHT_PROCESS_TIMEOUT: Duration = Duration::from_secs(3);
const MAX_PREFLIGHT_OUTPUT_BYTES: usize = 16 * 1024;
const MAX_PREFLIGHT_DETAIL_BYTES: usize = 1024;
const MAX_PREFLIGHT_PATH_BYTES: usize = 2048;
const MAX_RUNTIME_SETTINGS_BYTES: u64 = 1024 * 1024;
const MAX_PATH_SEARCH_ENTRIES: usize = 256;
const PREFLIGHT_SCHEMA_VERSION: u32 = 1;
const SETTINGS_PREFLIGHT_OK_TOKEN: &str = "MPP_SETTINGS_PREFLIGHT_V1_OK";
const SETTINGS_PREFLIGHT_INVALID_TOKEN: &str = "MPP_SETTINGS_PREFLIGHT_V1_INVALID";
const HEALTH_PRODUCT: &str = "com.mpp.backend";
const HEALTH_PROTOCOL: u32 = 1;
const HEALTH_SERVICE: &str = "Media Process Pipeline";
const RUNTIME_MANIFEST_SCHEMA: u32 = 1;
const RUNTIME_MANIFEST_FILE: &str = "runtime-manifest.json";
const TOOL_CONTRACT_PATH: &str = "packaging/desktop-tools.json";
#[cfg(not(test))]
const TRUSTED_TOOL_CONTRACT_JSON: &str = include_str!("../../../packaging/desktop-tools.json");
#[cfg(test)]
const TRUSTED_TOOL_CONTRACT_JSON: &str = r#"{
  "schema": 1,
  "tools": {
    "uv": {
      "version": "0.9.21",
      "binary": {
        "runtimePath": "bin/uv.exe",
        "sha256": "493a3a420f88fd28799ea5f61a39f89308d3bbbd7796bd98611367512b38dba9",
        "size": 70,
        "peMachine": "0x8664"
      }
    }
  }
}"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RuntimeMode {
    ExplicitProject,
    Portable,
    Installed,
    Development,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RuntimeResolutionPolicy {
    Production,
    Development,
}

impl RuntimeResolutionPolicy {
    fn current_build() -> Self {
        if cfg!(debug_assertions) {
            Self::Development
        } else {
            Self::Production
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct UserPaths {
    root: PathBuf,
    config_dir: PathBuf,
    config_file: PathBuf,
    runtime_dir: PathBuf,
    venv_dir: PathBuf,
    python_dir: PathBuf,
    cache_dir: PathBuf,
    log_dir: PathBuf,
    updates_dir: PathBuf,
    state_dir: PathBuf,
    data_dir: PathBuf,
    temp_dir: PathBuf,
}

impl UserPaths {
    fn new(root: PathBuf) -> Self {
        let config_dir = root.join("config");
        let runtime_dir = root.join("runtime");
        Self {
            config_file: config_dir.join("config.json"),
            venv_dir: runtime_dir.join(".venv"),
            python_dir: runtime_dir.join("python"),
            cache_dir: root.join("cache"),
            log_dir: root.join("logs"),
            updates_dir: root.join("updates"),
            state_dir: root.join("state"),
            data_dir: root.join("data"),
            temp_dir: runtime_dir.join("tmp"),
            config_dir,
            runtime_dir,
            root,
        }
    }

    fn required_directories(&self) -> [&Path; 8] {
        [
            &self.config_dir,
            &self.runtime_dir,
            &self.cache_dir,
            &self.log_dir,
            &self.updates_dir,
            &self.state_dir,
            &self.data_dir,
            &self.temp_dir,
        ]
    }
}

#[derive(Clone, Eq, PartialEq)]
struct RuntimeLayout {
    mode: RuntimeMode,
    runtime_root: PathBuf,
    backend_dir: PathBuf,
    web_dist_dir: PathBuf,
    uv_executable: OsString,
    session_token: String,
    proxy_token: String,
    backend_port: u16,
    user: UserPaths,
}

#[derive(Clone, Debug, Default)]
struct RuntimeCandidates {
    explicit_root: Option<PathBuf>,
    executable: Option<PathBuf>,
    resource_dir: Option<PathBuf>,
    manifest_dir: Option<PathBuf>,
    allow_manifest_fallback: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct BackendLaunchSpec {
    program: OsString,
    cwd: PathBuf,
    args: Vec<OsString>,
    env: BTreeMap<String, OsString>,
    clear_environment: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RuntimeManifest {
    schema: u32,
    app_version: String,
    source_commit: String,
    source_dirty: bool,
    tool_contract: String,
    uv: RuntimeUvRecord,
    files: Vec<RuntimeFileRecord>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RuntimeUvRecord {
    version: String,
    path: String,
    sha256: String,
    size: u64,
    pe_machine: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RuntimeFileRecord {
    path: String,
    size: u64,
    sha256: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum BackendProbe {
    Unavailable,
    Compatible,
    Incompatible(String),
    Occupied(String),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RequestBodyFraming {
    None,
    ContentLength(u64),
    Chunked,
}

#[derive(Debug)]
struct PreparedProxyRequest {
    head: Vec<u8>,
    body_prefix: Vec<u8>,
    body_framing: RequestBodyFraming,
    cors_origin: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ParsedHttpHeader {
    name: String,
    value: String,
}

#[derive(Debug, Eq, PartialEq)]
struct ParsedHttpRequest {
    method: String,
    path: String,
    headers: Vec<ParsedHttpHeader>,
}

#[derive(Debug, Eq, PartialEq)]
struct ParsedHttpResponse {
    status: u16,
    headers: Vec<ParsedHttpHeader>,
}

#[derive(Debug)]
enum ProxyRequestAction {
    Preflight(Vec<u8>),
    Forward(PreparedProxyRequest),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ProxyFailureKind {
    BadRequest,
    Unauthorized,
    Forbidden,
    MethodNotAllowed,
    TooManyRequests,
    BadGateway,
    ServiceUnavailable,
}

#[derive(Debug)]
struct ProxyFailure {
    kind: ProxyFailureKind,
    cors_origin: Option<String>,
    internal: String,
}

#[derive(Debug)]
struct HttpReadFailure {
    message: String,
    transient: bool,
}

impl HttpReadFailure {
    fn transient(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            transient: true,
        }
    }

    fn invalid(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            transient: false,
        }
    }
}

impl std::fmt::Display for HttpReadFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl ProxyFailure {
    fn new(kind: ProxyFailureKind, internal: impl Into<String>) -> Self {
        Self {
            kind,
            cors_origin: None,
            internal: internal.into(),
        }
    }

    fn with_cors_origin(mut self, origin: Option<&str>) -> Self {
        self.cors_origin = origin
            .filter(|value| trusted_cors_origin(value))
            .map(str::to_string);
        self
    }
}

struct ProxyListeners {
    ipv4: TcpListener,
    ipv6: Option<TcpListener>,
}

struct ProxyConnectionPermit {
    active: Arc<AtomicUsize>,
}

impl Drop for ProxyConnectionPermit {
    fn drop(&mut self) {
        self.active.fetch_sub(1, Ordering::AcqRel);
    }
}

#[derive(Debug, Deserialize)]
struct HealthPayload {
    status: String,
    product: String,
    protocol: u32,
    service: String,
    version: String,
    #[serde(rename = "desktopProof")]
    desktop_proof: Option<String>,
}

#[derive(Clone, Serialize)]
struct BackendStatus {
    state: String,
    command: String,
    cwd: String,
    pid: Option<u32>,
    url: String,
    message: String,
    phase: String,
    error_code: Option<String>,
    component_id: Option<String>,
    remediation: Option<String>,
    local_path: Option<String>,
}

#[derive(Clone, Serialize)]
struct BackendLogEntry {
    ts: String,
    source: String,
    line: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
struct BootstrapPreflightReport {
    schema_version: u32,
    overall_status: String,
    components: Vec<BootstrapPreflightComponent>,
}

impl Default for BootstrapPreflightReport {
    fn default() -> Self {
        scanning_preflight_report()
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
struct BootstrapPreflightComponent {
    component_id: String,
    label: String,
    status: String,
    required: bool,
    version: Option<String>,
    path: Option<String>,
    error_code: Option<String>,
    remediation: Option<String>,
    detail: Option<String>,
}

#[derive(Debug)]
struct ProbeCommandOutput {
    success: bool,
    output: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SettingsPreflightOutcome {
    Valid,
    Invalid,
    Error,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum PythonVersionOutcome {
    Supported(String),
    Unsupported(String),
    Invalid,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ProbeCommandFailureKind {
    PermissionDenied,
    Spawn,
    Timeout,
    Supervision,
}

#[derive(Debug)]
struct ProbeCommandFailure {
    kind: ProbeCommandFailureKind,
    detail: String,
}

#[derive(Debug)]
enum ProbeExecutableResolution {
    Found(PathBuf),
    Missing,
    Invalid(PathBuf, String),
}

struct ProcessJob {
    handle: Option<usize>,
}

impl ProcessJob {
    fn terminate_tree(&mut self) -> bool {
        if let Some(handle) = self.handle.take() {
            close_job_handle(handle);
            true
        } else {
            false
        }
    }
}

impl Drop for ProcessJob {
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            close_job_handle(handle);
        }
    }
}

struct ManagedBackend {
    child: Child,
    job: ProcessJob,
    generation: u64,
}

#[derive(Default)]
struct BackendLifecycle {
    managed: Option<ManagedBackend>,
    generation: u64,
}

struct BackendProcess {
    lifecycle: Mutex<BackendLifecycle>,
    status: Mutex<BackendStatus>,
    logs: Mutex<Vec<BackendLogEntry>>,
}

impl Default for BackendProcess {
    fn default() -> Self {
        Self {
            lifecycle: Mutex::new(BackendLifecycle::default()),
            status: Mutex::new(BackendStatus {
                state: "starting".to_string(),
                command: BACKEND_COMMAND.to_string(),
                cwd: "backend".to_string(),
                pid: None,
                url: APP_URL.to_string(),
                message: "Scanning the installed runtime.".to_string(),
                phase: "SCANNING".to_string(),
                error_code: None,
                component_id: None,
                remediation: None,
                local_path: None,
            }),
            logs: Mutex::new(Vec::new()),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct BootstrapFailure {
    retryable: bool,
    error_code: String,
    component_id: String,
    remediation: String,
    detail: String,
    local_path: Option<String>,
}

impl BootstrapFailure {
    fn retryable(
        error_code: impl Into<String>,
        component_id: impl Into<String>,
        remediation: impl Into<String>,
        detail: impl Into<String>,
        local_path: Option<&Path>,
    ) -> Self {
        Self {
            retryable: true,
            error_code: error_code.into(),
            component_id: component_id.into(),
            remediation: remediation.into(),
            detail: detail.into(),
            local_path: local_path.map(|path| path.to_string_lossy().into_owned()),
        }
    }

    fn manual(
        error_code: impl Into<String>,
        component_id: impl Into<String>,
        remediation: impl Into<String>,
        detail: impl Into<String>,
        local_path: Option<&Path>,
    ) -> Self {
        Self {
            retryable: false,
            error_code: error_code.into(),
            component_id: component_id.into(),
            remediation: remediation.into(),
            detail: detail.into(),
            local_path: local_path.map(|path| path.to_string_lossy().into_owned()),
        }
    }

    fn phase(&self) -> &'static str {
        if self.retryable {
            "FAILED_RETRYABLE"
        } else {
            "FAILED_MANUAL"
        }
    }
}

#[derive(Default)]
struct BootstrapRuntime {
    layout: Option<RuntimeLayout>,
    proxy_started: bool,
    attempt_running: bool,
    attempt_epoch: u64,
    bootstrap_complete: bool,
    shutdown_requested: bool,
    preflight: BootstrapPreflightReport,
}

struct BootstrapController {
    runtime: Mutex<BootstrapRuntime>,
    fallback_log_dir: Option<PathBuf>,
}

impl BootstrapController {
    fn new(fallback_log_dir: Option<PathBuf>) -> Self {
        Self {
            runtime: Mutex::new(BootstrapRuntime::default()),
            fallback_log_dir,
        }
    }
}

const PREFLIGHT_COMPONENT_ORDER: [&str; 10] = [
    "desktop-runtime",
    "data-root",
    "bundled-uv",
    "python-environment",
    "ffmpeg",
    "ffprobe",
    "desktop-proxy-port",
    "backend-private-port",
    "runtime-settings",
    "webview2",
];

fn canonical_preflight_component_id(component_id: &str) -> &str {
    match component_id {
        "python-runtime" => "python-environment",
        "backend-health" => "python-environment",
        "desktop-proxy" => "desktop-proxy-port",
        "backend-port" => "backend-private-port",
        "desktop-webview" => "webview2",
        "desktop-bootstrap" | "desktop-session" => "desktop-runtime",
        value if PREFLIGHT_COMPONENT_ORDER.contains(&value) => value,
        _ => "desktop-runtime",
    }
}

fn preflight_component_label(component_id: &str) -> &str {
    match component_id {
        "desktop-runtime" => "Desktop Runtime",
        "data-root" => "Local Data Root",
        "bundled-uv" => "uv Runtime Manager",
        "python-environment" => "Python Environment",
        "ffmpeg" => "FFmpeg",
        "ffprobe" => "FFprobe",
        "desktop-proxy-port" => "Desktop Proxy Port",
        "backend-private-port" => "Backend Private Port",
        "runtime-settings" => "Runtime Settings",
        "webview2" => "Microsoft Edge WebView2",
        _ => "Desktop Bootstrap",
    }
}

fn bounded_preflight_text(value: impl AsRef<str>, maximum_bytes: usize) -> String {
    let value = value.as_ref();
    if value.len() <= maximum_bytes {
        return value.to_string();
    }
    let suffix = "...";
    let content_limit = maximum_bytes.saturating_sub(suffix.len());
    let mut boundary = content_limit.min(value.len());
    while boundary > 0 && !value.is_char_boundary(boundary) {
        boundary -= 1;
    }
    format!("{}{}", &value[..boundary], suffix)
}

fn bounded_preflight_path(path: &Path) -> String {
    bounded_preflight_text(path.to_string_lossy(), MAX_PREFLIGHT_PATH_BYTES)
}

fn preflight_component(
    component_id: &str,
    label: &str,
    status: &str,
    required: bool,
    version: Option<String>,
    path: Option<&Path>,
    error_code: Option<&str>,
    remediation: Option<&str>,
    detail: Option<String>,
) -> BootstrapPreflightComponent {
    BootstrapPreflightComponent {
        component_id: component_id.to_string(),
        label: label.to_string(),
        status: status.to_string(),
        required,
        version: version.map(|value| bounded_preflight_text(value, 128)),
        path: path.map(bounded_preflight_path),
        error_code: error_code.map(str::to_string),
        remediation: remediation
            .map(|value| bounded_preflight_text(value, MAX_PREFLIGHT_DETAIL_BYTES)),
        detail: detail
            .filter(|value| !value.is_empty())
            .map(|value| bounded_preflight_text(value, MAX_PREFLIGHT_DETAIL_BYTES)),
    }
}

fn scanning_preflight_report() -> BootstrapPreflightReport {
    BootstrapPreflightReport {
        schema_version: PREFLIGHT_SCHEMA_VERSION,
        overall_status: "scanning".to_string(),
        components: PREFLIGHT_COMPONENT_ORDER
            .iter()
            .map(|component_id| {
                preflight_component(
                    component_id,
                    preflight_component_label(component_id),
                    "scanning",
                    true,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            })
            .collect(),
    }
}

fn preflight_component_rank(component_id: &str) -> (usize, &str) {
    (
        PREFLIGHT_COMPONENT_ORDER
            .iter()
            .position(|candidate| *candidate == component_id)
            .unwrap_or(PREFLIGHT_COMPONENT_ORDER.len()),
        component_id,
    )
}

fn finalize_preflight_report(
    mut components: Vec<BootstrapPreflightComponent>,
) -> BootstrapPreflightReport {
    components.sort_by(|left, right| {
        preflight_component_rank(&left.component_id)
            .cmp(&preflight_component_rank(&right.component_id))
    });
    let overall_status = if components
        .iter()
        .any(|component| component.required && component.status == "blocked")
    {
        "blocked"
    } else if components.iter().any(|component| {
        component.required && matches!(component.status.as_str(), "missing" | "invalid")
    }) {
        "needs_repair"
    } else if components
        .iter()
        .any(|component| component.status != "ready")
    {
        "needs_configuration"
    } else {
        "ready"
    };
    BootstrapPreflightReport {
        schema_version: PREFLIGHT_SCHEMA_VERSION,
        overall_status: overall_status.to_string(),
        components,
    }
}

fn preflight_blocking_failure(report: &BootstrapPreflightReport) -> Option<BootstrapFailure> {
    let component = report.components.iter().find(|component| {
        component.required && matches!(component.status.as_str(), "missing" | "invalid" | "blocked")
    })?;
    let retryable = component.status == "blocked"
        || matches!(
            component.component_id.as_str(),
            "data-root" | "desktop-proxy-port" | "backend-private-port"
        );
    Some(BootstrapFailure {
        retryable,
        error_code: component
            .error_code
            .clone()
            .unwrap_or_else(|| "PREFLIGHT_REQUIRED_COMPONENT_FAILED".to_string()),
        component_id: component.component_id.clone(),
        remediation: component
            .remediation
            .clone()
            .unwrap_or_else(|| "Repair the reported desktop component and retry.".to_string()),
        detail: component
            .detail
            .clone()
            .unwrap_or_else(|| format!("{} did not pass preflight.", component.label)),
        local_path: component.path.clone(),
    })
}

fn apply_failure_to_preflight(report: &mut BootstrapPreflightReport, failure: &BootstrapFailure) {
    let component_id = canonical_preflight_component_id(&failure.component_id);
    let failure_status = if failure.error_code.ends_with("_MISSING") {
        "missing"
    } else if failure.retryable {
        "blocked"
    } else {
        "invalid"
    };
    if let Some(component) = report
        .components
        .iter_mut()
        .find(|component| component.component_id == component_id)
    {
        component.status = failure_status.to_string();
        component.required = true;
        component.error_code = Some(failure.error_code.clone());
        component.remediation = Some(bounded_preflight_text(
            &failure.remediation,
            MAX_PREFLIGHT_DETAIL_BYTES,
        ));
        component.detail = Some(bounded_preflight_text(
            &failure.detail,
            MAX_PREFLIGHT_DETAIL_BYTES,
        ));
        if let Some(path) = failure.local_path.as_deref() {
            component.path = Some(bounded_preflight_text(path, MAX_PREFLIGHT_PATH_BYTES));
        }
    }
    *report = finalize_preflight_report(std::mem::take(&mut report.components));
}

fn boxed_error(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(std::io::Error::new(
        std::io::ErrorKind::Other,
        message.into(),
    ))
}

fn has_runtime_markers(path: &Path) -> bool {
    path.join("pyproject.toml").is_file() && path.join("backend").join("app").is_dir()
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn validate_manifest_relative_path(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.starts_with('/')
        || value.contains('\\')
        || value.contains('\0')
        || value.contains(':')
    {
        return Err(format!("invalid manifest path: {value:?}"));
    }
    if value
        .split('/')
        .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err(format!("invalid manifest path: {value:?}"));
    }
    Ok(())
}

fn metadata_is_link_or_reparse(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    false
}

fn validate_existing_path_chain(path: &Path, label: &str) -> Result<(), String> {
    if !path.is_absolute() {
        return Err(format!("{label} must be absolute: {}", path.display()));
    }
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        if !current.is_absolute() {
            continue;
        }
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata_is_link_or_reparse(&metadata) => {
                return Err(format!(
                    "{label} contains a symlink, junction, or reparse point at {}",
                    current.display()
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(format!(
                    "cannot inspect {label} ancestor {}: {error}",
                    current.display()
                ));
            }
        }
    }
    Ok(())
}

fn runtime_relative_path(root: &Path, path: &Path) -> Result<String, String> {
    let relative = path.strip_prefix(root).map_err(|error| {
        format!(
            "runtime entry {} escapes {}: {error}",
            path.display(),
            root.display()
        )
    })?;
    let mut parts = Vec::new();
    for component in relative.components() {
        match component {
            std::path::Component::Normal(part) => parts.push(
                part.to_str()
                    .ok_or_else(|| {
                        format!("runtime entry has a non-UTF-8 path: {}", path.display())
                    })?
                    .to_string(),
            ),
            _ => {
                return Err(format!(
                    "runtime entry has an invalid relative path: {}",
                    path.display()
                ));
            }
        }
    }
    let value = parts.join("/");
    validate_manifest_relative_path(&value)?;
    Ok(value)
}

fn collect_runtime_files(root: &Path) -> Result<BTreeMap<String, PathBuf>, String> {
    validate_existing_path_chain(root, "installed runtime path")?;
    let root_metadata = fs::symlink_metadata(root)
        .map_err(|error| format!("cannot inspect runtime root {}: {error}", root.display()))?;
    if metadata_is_link_or_reparse(&root_metadata) || !root_metadata.is_dir() {
        return Err(format!(
            "runtime root must be a real directory without symlinks, junctions, or reparse \
             points: {}",
            root.display()
        ));
    }

    let mut files = BTreeMap::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let entries = fs::read_dir(&directory).map_err(|error| {
            format!(
                "cannot read runtime directory {}: {error}",
                directory.display()
            )
        })?;
        for entry in entries {
            let entry = entry.map_err(|error| {
                format!(
                    "cannot read an entry in runtime directory {}: {error}",
                    directory.display()
                )
            })?;
            let path = entry.path();
            let relative = runtime_relative_path(root, &path)?;
            let metadata = fs::symlink_metadata(&path)
                .map_err(|error| format!("cannot inspect runtime entry {relative}: {error}"))?;
            if metadata_is_link_or_reparse(&metadata) {
                return Err(format!(
                    "runtime symlink, junction, or reparse point is not allowed: {relative}"
                ));
            }
            if metadata.is_dir() {
                pending.push(path);
            } else if metadata.is_file() {
                if files.insert(relative.clone(), path).is_some() {
                    return Err(format!("duplicate runtime path: {relative}"));
                }
            } else {
                return Err(format!("unsupported runtime entry: {relative}"));
            }
        }
    }
    Ok(files)
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

fn json_string<'a>(value: &'a serde_json::Value, pointer: &str) -> Result<&'a str, String> {
    value
        .pointer(pointer)
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("trusted desktop tool contract is missing {pointer}"))
}

fn json_u64(value: &serde_json::Value, pointer: &str) -> Result<u64, String> {
    value
        .pointer(pointer)
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("trusted desktop tool contract is missing {pointer}"))
}

fn pe_machine(path: &Path) -> Result<String, String> {
    let mut file =
        fs::File::open(path).map_err(|error| format!("cannot open {}: {error}", path.display()))?;
    let mut dos_header = [0_u8; 64];
    file.read_exact(&mut dos_header)
        .map_err(|error| format!("invalid uv DOS header: {error}"))?;
    if &dos_header[..2] != b"MZ" {
        return Err("bundled uv is missing the DOS MZ header".to_string());
    }
    let pe_offset = u32::from_le_bytes(
        dos_header[0x3c..0x40]
            .try_into()
            .expect("fixed DOS header slice"),
    ) as u64;
    if !(64..=16 * 1024 * 1024).contains(&pe_offset) {
        return Err(format!("bundled uv has an invalid PE offset: {pe_offset}"));
    }
    use std::io::{Seek, SeekFrom};
    file.seek(SeekFrom::Start(pe_offset))
        .map_err(|error| format!("cannot seek to the uv PE header: {error}"))?;
    let mut pe_header = [0_u8; 6];
    file.read_exact(&mut pe_header)
        .map_err(|error| format!("invalid uv PE header: {error}"))?;
    if &pe_header[..4] != b"PE\0\0" {
        return Err("bundled uv is missing the PE signature".to_string());
    }
    Ok(format!(
        "0x{:04x}",
        u16::from_le_bytes([pe_header[4], pe_header[5]])
    ))
}

fn validate_installed_runtime_manifest_with_hash(
    root: &Path,
    expected_manifest_hash: &str,
) -> Result<(), String> {
    let mut actual_files = collect_runtime_files(root)?;
    let manifest_path = actual_files
        .remove(RUNTIME_MANIFEST_FILE)
        .ok_or_else(|| format!("{RUNTIME_MANIFEST_FILE} is missing"))?;
    let manifest_bytes = fs::read(&manifest_path)
        .map_err(|error| format!("cannot read {RUNTIME_MANIFEST_FILE}: {error}"))?;
    if !is_lower_hex(expected_manifest_hash, 64) {
        return Err(
            "desktop was built without a trusted installed runtime manifest hash".to_string(),
        );
    }
    let actual_manifest_hash = format!("{:x}", Sha256::digest(&manifest_bytes));
    if actual_manifest_hash != expected_manifest_hash {
        return Err(format!(
            "{RUNTIME_MANIFEST_FILE} SHA-256 differs from the desktop build"
        ));
    }
    let manifest: RuntimeManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|error| format!("{RUNTIME_MANIFEST_FILE} is invalid: {error}"))?;

    if manifest.schema != RUNTIME_MANIFEST_SCHEMA {
        return Err(format!(
            "runtime manifest schema must be {RUNTIME_MANIFEST_SCHEMA}; received {}",
            manifest.schema
        ));
    }
    if manifest.app_version != env!("CARGO_PKG_VERSION") {
        return Err(format!(
            "runtime appVersion {} differs from desktop {}",
            manifest.app_version,
            env!("CARGO_PKG_VERSION")
        ));
    }
    let version = fs::read_to_string(root.join("VERSION"))
        .map_err(|error| format!("cannot read runtime VERSION: {error}"))?;
    if version.trim() != manifest.app_version {
        return Err("runtime VERSION differs from manifest appVersion".to_string());
    }
    if !is_lower_hex(&manifest.source_commit, 40) {
        return Err("runtime sourceCommit must be a full lowercase Git SHA".to_string());
    }
    if manifest.source_commit != env!("MPP_BUILD_COMMIT") {
        return Err(format!(
            "runtime sourceCommit {} differs from desktop build {}",
            manifest.source_commit,
            env!("MPP_BUILD_COMMIT")
        ));
    }
    if manifest.source_dirty {
        return Err("runtime sourceDirty must be false".to_string());
    }
    if manifest.tool_contract != TOOL_CONTRACT_PATH {
        return Err(format!("runtime toolContract must be {TOOL_CONTRACT_PATH}"));
    }

    let mut declared = BTreeMap::new();
    for record in &manifest.files {
        validate_manifest_relative_path(&record.path)?;
        if record.path == RUNTIME_MANIFEST_FILE {
            return Err(format!(
                "{RUNTIME_MANIFEST_FILE} must not declare itself in files"
            ));
        }
        if !is_lower_hex(&record.sha256, 64) {
            return Err(format!(
                "runtime file {} has an invalid SHA-256",
                record.path
            ));
        }
        if declared.insert(record.path.clone(), record).is_some() {
            return Err(format!("duplicate manifest path: {}", record.path));
        }
    }

    let actual_set = actual_files.keys().cloned().collect::<BTreeSet<_>>();
    let declared_set = declared.keys().cloned().collect::<BTreeSet<_>>();
    let missing = declared_set
        .difference(&actual_set)
        .cloned()
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Err(format!(
            "manifest-declared runtime files are missing: {}",
            missing.join(", ")
        ));
    }
    let extra = actual_set
        .difference(&declared_set)
        .cloned()
        .collect::<Vec<_>>();
    if !extra.is_empty() {
        return Err(format!(
            "unlisted runtime files are present: {}",
            extra.join(", ")
        ));
    }

    for (relative, path) in &actual_files {
        let record = declared
            .get(relative)
            .expect("actual and declared sets were compared");
        let metadata = fs::metadata(path)
            .map_err(|error| format!("cannot inspect runtime file {relative}: {error}"))?;
        if metadata.len() != record.size {
            return Err(format!(
                "runtime file size mismatch for {relative}: expected {}, received {}",
                record.size,
                metadata.len()
            ));
        }
        let actual_hash = file_sha256(path)?;
        if actual_hash != record.sha256 {
            return Err(format!("runtime file SHA-256 mismatch for {relative}"));
        }
    }

    let trusted_contract: serde_json::Value = serde_json::from_str(TRUSTED_TOOL_CONTRACT_JSON)
        .map_err(|error| format!("desktop contains an invalid trusted tool contract: {error}"))?;
    let staged_contract_path = actual_files
        .get(TOOL_CONTRACT_PATH)
        .ok_or_else(|| format!("required runtime file is missing: {TOOL_CONTRACT_PATH}"))?;
    let staged_contract: serde_json::Value = serde_json::from_slice(
        &fs::read(staged_contract_path)
            .map_err(|error| format!("cannot read staged desktop tool contract: {error}"))?,
    )
    .map_err(|error| format!("staged desktop tool contract is invalid: {error}"))?;
    if staged_contract != trusted_contract {
        return Err("staged desktop tool contract differs from the compiled contract".to_string());
    }

    let trusted_uv_version = json_string(&trusted_contract, "/tools/uv/version")?;
    let trusted_uv_path = json_string(&trusted_contract, "/tools/uv/binary/runtimePath")?;
    let trusted_uv_hash = json_string(&trusted_contract, "/tools/uv/binary/sha256")?;
    let trusted_uv_size = json_u64(&trusted_contract, "/tools/uv/binary/size")?;
    let trusted_uv_machine = json_string(&trusted_contract, "/tools/uv/binary/peMachine")?;
    if manifest.uv.version != trusted_uv_version
        || manifest.uv.path != trusted_uv_path
        || manifest.uv.sha256 != trusted_uv_hash
        || manifest.uv.size != trusted_uv_size
        || manifest.uv.pe_machine != trusted_uv_machine
    {
        return Err("runtime uv metadata differs from the compiled tool contract".to_string());
    }
    validate_manifest_relative_path(&manifest.uv.path)?;
    let uv_record = declared
        .get(&manifest.uv.path)
        .ok_or_else(|| "bundled uv is absent from the runtime file manifest".to_string())?;
    if uv_record.size != manifest.uv.size || uv_record.sha256 != manifest.uv.sha256 {
        return Err("runtime uv metadata differs from its file record".to_string());
    }
    let uv_path = actual_files
        .get(&manifest.uv.path)
        .ok_or_else(|| format!("bundled uv is missing: {}", manifest.uv.path))?;
    if pe_machine(uv_path)? != manifest.uv.pe_machine {
        return Err("bundled uv PE machine differs from the runtime manifest".to_string());
    }

    for required in [
        "pyproject.toml",
        "uv.lock",
        "VERSION",
        "backend/app/__init__.py",
        "web/dist/index.html",
        TOOL_CONTRACT_PATH,
        "third-party-licenses/uv-LICENSE-MIT.txt",
        "bin/uv.exe",
    ] {
        if !actual_files.contains_key(required) {
            return Err(format!("required runtime file is missing: {required}"));
        }
    }
    Ok(())
}

#[cfg(not(test))]
fn validate_installed_runtime_manifest(root: &Path) -> Result<(), String> {
    validate_installed_runtime_manifest_with_hash(root, env!("MPP_RUNTIME_MANIFEST_SHA256"))
}

#[cfg(test)]
fn validate_installed_runtime_manifest(root: &Path) -> Result<(), String> {
    let manifest_hash = file_sha256(&root.join(RUNTIME_MANIFEST_FILE))?;
    validate_installed_runtime_manifest_with_hash(root, &manifest_hash)
}

fn validate_runtime_root(path: &Path, installed: bool) -> Result<(), String> {
    let required_files = [
        "pyproject.toml",
        "uv.lock",
        "VERSION",
        "web/dist/index.html",
    ];
    let mut missing = required_files
        .iter()
        .filter(|relative| !path.join(relative).is_file())
        .map(|relative| (*relative).to_string())
        .collect::<Vec<_>>();

    if !path.join("backend").join("app").is_dir() {
        missing.push("backend/app/".to_string());
    }
    if !missing.is_empty() {
        Err(format!(
            "runtime root is incomplete at {}: missing {}",
            path.display(),
            missing.join(", ")
        ))
    } else if installed {
        validate_installed_runtime_manifest(path)
            .map_err(|error| format!("installed runtime validation failed: {error}"))
    } else {
        Ok(())
    }
}

fn resolve_runtime_candidate(
    candidates: &RuntimeCandidates,
    policy: RuntimeResolutionPolicy,
) -> Result<(RuntimeMode, PathBuf), String> {
    if policy == RuntimeResolutionPolicy::Production {
        if candidates.explicit_root.is_some() {
            return Err("release desktop rejects MPP_PROJECT_ROOT".to_string());
        }
        let resource_dir = candidates.resource_dir.as_deref().ok_or_else(|| {
            "release desktop requires the signed Tauri resource directory".to_string()
        })?;
        let candidate = resource_dir.join("runtime");
        if !candidate.exists() {
            return Err(format!(
                "release desktop runtime is missing from {}",
                candidate.display()
            ));
        }
        validate_runtime_root(&candidate, true)?;
        return Ok((RuntimeMode::Installed, candidate));
    }

    if let Some(candidate) = candidates.explicit_root.as_deref() {
        validate_runtime_root(candidate, false).map_err(|error| {
            format!("MPP_PROJECT_ROOT does not contain a complete runtime: {error}")
        })?;
        return Ok((RuntimeMode::ExplicitProject, candidate.to_path_buf()));
    }

    if let Some(executable) = candidates.executable.as_deref() {
        for ancestor in executable.ancestors() {
            if has_runtime_markers(ancestor) {
                validate_runtime_root(ancestor, false)?;
                return Ok((RuntimeMode::Portable, ancestor.to_path_buf()));
            }
        }
    }

    if let Some(resource_dir) = candidates.resource_dir.as_deref() {
        let candidate = resource_dir.join("runtime");
        if candidate.exists() {
            validate_runtime_root(&candidate, true)?;
            return Ok((RuntimeMode::Installed, candidate));
        }
    }

    if candidates.allow_manifest_fallback {
        if let Some(manifest_dir) = candidates.manifest_dir.as_deref() {
            let candidate = manifest_dir
                .parent()
                .and_then(Path::parent)
                .ok_or_else(|| {
                    format!(
                        "cannot derive project root from manifest directory {}",
                        manifest_dir.display()
                    )
                })?
                .to_path_buf();
            validate_runtime_root(&candidate, false)?;
            return Ok((RuntimeMode::Development, candidate));
        }
    }

    Err(
        "could not resolve a complete MediaProcessPipeline runtime from the explicit project, \
         portable executable, Tauri resources, or debug manifest"
            .to_string(),
    )
}

fn validate_release_environment_overrides(
    policy: RuntimeResolutionPolicy,
    configured_uv: Option<&OsString>,
    explicit_user_root: Option<&PathBuf>,
) -> Result<(), String> {
    if policy == RuntimeResolutionPolicy::Production {
        if configured_uv.is_some() {
            return Err("release desktop rejects MPP_UV".to_string());
        }
        if explicit_user_root.is_some() {
            return Err("release desktop rejects MPP_USER_ROOT".to_string());
        }
    }
    Ok(())
}

fn resolve_user_paths(
    explicit_root: Option<PathBuf>,
    local_data_dir: PathBuf,
) -> Result<UserPaths, String> {
    let root = explicit_root.unwrap_or_else(|| local_data_dir.join("MediaProcessPipeline"));
    if !root.is_absolute() {
        return Err(format!(
            "MPP_USER_ROOT must be an absolute path: {}",
            root.display()
        ));
    }
    Ok(UserPaths::new(root))
}

fn ensure_user_directories(paths: &UserPaths) -> Result<(), String> {
    validate_existing_path_chain(&paths.root, "MediaProcessPipeline user root")?;

    for path in paths.required_directories() {
        validate_existing_path_chain(path, "MediaProcessPipeline user directory")?;
        fs::create_dir_all(path).map_err(|error| {
            format!(
                "failed to create MediaProcessPipeline user directory {}: {error}",
                path.display()
            )
        })?;
        validate_existing_path_chain(path, "MediaProcessPipeline user directory")?;
        let metadata = fs::symlink_metadata(path).map_err(|error| {
            format!(
                "failed to inspect MediaProcessPipeline user directory {}: {error}",
                path.display()
            )
        })?;
        if metadata_is_link_or_reparse(&metadata) || !metadata.is_dir() {
            return Err(format!(
                "MediaProcessPipeline user directory must be a real directory: {}",
                path.display()
            ));
        }
    }

    for (index, directory) in std::iter::once(paths.root.as_path())
        .chain(paths.required_directories())
        .enumerate()
    {
        let probe_path = directory.join(format!(
            ".write-probe-{}-{}-{index}",
            std::process::id(),
            Utc::now().timestamp_micros()
        ));
        let probe_result = (|| -> std::io::Result<()> {
            let mut probe = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&probe_path)?;
            probe.write_all(b"mpp")?;
            probe.sync_all()
        })();
        let _ = fs::remove_file(&probe_path);
        probe_result.map_err(|error| {
            format!(
                "MediaProcessPipeline user directory is not writable at {}: {error}",
                directory.display()
            )
        })?;
    }

    Ok(())
}

fn generate_session_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| format!("failed to generate the desktop session token: {error}"))?;
    let mut token = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut token, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(token)
}

fn select_private_backend_port() -> Result<u16, String> {
    for _ in 0..32 {
        let ipv4 = bind_proxy_listener(SocketAddr::from((Ipv4Addr::LOCALHOST, 0)))
            .map_err(|error| format!("failed to reserve an IPv4 backend port: {error}"))?;
        let port = ipv4
            .local_addr()
            .map_err(|error| format!("failed to inspect the private backend port: {error}"))?
            .port();
        match bind_proxy_listener(SocketAddr::from((Ipv6Addr::LOCALHOST, port))) {
            Ok(ipv6) => {
                drop(ipv6);
                drop(ipv4);
                return Ok(port);
            }
            Err(error) if ipv6_listener_is_unavailable(&error) => {
                drop(ipv4);
                return Ok(port);
            }
            Err(_) => continue,
        }
    }
    Err("could not select a private dual-loopback backend port".to_string())
}

fn runtime_layout_from_parts(
    mode: RuntimeMode,
    runtime_root: PathBuf,
    user: UserPaths,
    configured_uv: Option<OsString>,
    session_token: String,
    proxy_token: String,
    backend_port: u16,
) -> RuntimeLayout {
    let uv_executable = if mode == RuntimeMode::Installed {
        runtime_root.join("bin").join("uv.exe").into_os_string()
    } else {
        configured_uv.unwrap_or_else(|| OsString::from("uv"))
    };
    RuntimeLayout {
        backend_dir: runtime_root.join("backend"),
        web_dist_dir: runtime_root.join("web").join("dist"),
        runtime_root,
        uv_executable,
        session_token,
        proxy_token,
        backend_port,
        user,
        mode,
    }
}

fn runtime_resolution_failure(error: String, runtime_hint: Option<&Path>) -> BootstrapFailure {
    let lower = error.to_ascii_lowercase();
    let uv_related = lower.contains("bin/uv.exe")
        || lower.contains("bundled uv")
        || lower.contains("runtime uv metadata")
        || lower.contains("uv pe ")
        || lower.contains("uv dos ")
        || lower.contains("/tools/uv/");
    if uv_related {
        let missing = lower.contains("missing") || lower.contains("absent");
        let uv_path = runtime_hint.map(|path| path.join("bin").join("uv.exe"));
        return BootstrapFailure::manual(
            if missing { "UV_MISSING" } else { "UV_INVALID" },
            "bundled-uv",
            "Repair or reinstall the bundled uv runtime, then retry.",
            error,
            uv_path.as_deref(),
        );
    }
    BootstrapFailure::manual(
        "RUNTIME_INVALID",
        "desktop-runtime",
        "Repair or reinstall MediaProcessPipeline, then retry.",
        error,
        runtime_hint,
    )
}

fn resolve_runtime_layout(app: &AppHandle) -> Result<RuntimeLayout, BootstrapFailure> {
    let policy = RuntimeResolutionPolicy::current_build();
    let explicit_root = env::var_os("MPP_PROJECT_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let explicit_user_root = env::var_os("MPP_USER_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let configured_uv = env::var_os("MPP_UV").filter(|value| !value.is_empty());
    validate_release_environment_overrides(
        policy,
        configured_uv.as_ref(),
        explicit_user_root.as_ref(),
    )
    .map_err(|error| {
        BootstrapFailure::manual(
            "RUNTIME_CONFIGURATION_REJECTED",
            "desktop-runtime",
            "Remove unsupported desktop runtime overrides and restart the application.",
            error,
            explicit_root.as_deref(),
        )
    })?;
    let executable = env::current_exe().ok();
    let resource_dir = app.path().resource_dir().ok();
    let runtime_hint = resource_dir.as_ref().map(|path| path.join("runtime"));
    let local_data_dir = app.path().local_data_dir().map_err(|error| {
        BootstrapFailure::retryable(
            "DATA_ROOT_UNAVAILABLE",
            "data-root",
            "Check the Windows user profile and local application data directory, then retry.",
            format!("failed to resolve the Windows local data directory: {error}"),
            None,
        )
    })?;
    let candidates = RuntimeCandidates {
        explicit_root,
        executable,
        resource_dir,
        manifest_dir: Some(PathBuf::from(env!("CARGO_MANIFEST_DIR"))),
        allow_manifest_fallback: cfg!(debug_assertions),
    };
    let (mode, runtime_root) = resolve_runtime_candidate(&candidates, policy)
        .map_err(|error| runtime_resolution_failure(error, runtime_hint.as_deref()))?;
    let user = resolve_user_paths(explicit_user_root, local_data_dir).map_err(|error| {
        BootstrapFailure::retryable(
            "DATA_ROOT_UNWRITABLE",
            "data-root",
            "Choose or restore a writable local data directory, then retry.",
            error,
            None,
        )
    })?;
    if mode == RuntimeMode::Installed {
        ensure_user_directories(&user).map_err(|error| {
            BootstrapFailure::retryable(
                "DATA_ROOT_UNWRITABLE",
                "data-root",
                "Restore write access to the local data directory, then retry.",
                error,
                Some(&user.root),
            )
        })?;
    }
    let session_token = generate_session_token().map_err(|error| {
        BootstrapFailure::retryable(
            "DESKTOP_SESSION_INIT_FAILED",
            "desktop-session",
            "Restart the application. If the problem continues, open diagnostics.",
            error,
            Some(&user.state_dir),
        )
    })?;
    let proxy_token = generate_session_token().map_err(|error| {
        BootstrapFailure::retryable(
            "DESKTOP_SESSION_INIT_FAILED",
            "desktop-session",
            "Restart the application. If the problem continues, open diagnostics.",
            error,
            Some(&user.state_dir),
        )
    })?;
    let backend_port = select_private_backend_port().map_err(|error| {
        BootstrapFailure::retryable(
            "PRIVATE_PORT_UNAVAILABLE",
            "backend-port",
            "Close conflicting local services and retry.",
            error,
            None,
        )
    })?;
    Ok(runtime_layout_from_parts(
        mode,
        runtime_root,
        user,
        configured_uv,
        session_token,
        proxy_token,
        backend_port,
    ))
}

fn current_runtime_layout(app: &AppHandle) -> Result<RuntimeLayout, String> {
    let controller = app.state::<BootstrapController>();
    let layout = controller
        .runtime
        .lock()
        .map_err(|_| "bootstrap runtime lock poisoned".to_string())?
        .layout
        .clone();
    layout.ok_or_else(|| "desktop runtime is still being initialized".to_string())
}

fn bootstrap_controls_status(runtime: &BootstrapRuntime) -> bool {
    runtime.attempt_running || !runtime.bootstrap_complete || runtime.shutdown_requested
}

fn bootstrap_controls_status_for_app(app: &AppHandle) -> Result<bool, String> {
    let controller = app.state::<BootstrapController>();
    let runtime = controller
        .runtime
        .lock()
        .map_err(|_| "bootstrap runtime lock poisoned".to_string())?;
    Ok(bootstrap_controls_status(&runtime))
}

fn path_env(path: &Path) -> OsString {
    path.as_os_str().to_os_string()
}

fn safe_inherited_environment<I>(variables: I) -> BTreeMap<String, OsString>
where
    I: IntoIterator<Item = (OsString, OsString)>,
{
    const ALLOWED: [&str; 8] = [
        "COMSPEC",
        "CUDA_PATH",
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
    ];
    let mut inherited = BTreeMap::new();
    for (name, value) in variables {
        let name_text = name.to_string_lossy();
        if let Some(canonical) = ALLOWED
            .iter()
            .find(|allowed| name_text.eq_ignore_ascii_case(allowed))
        {
            inherited.insert((*canonical).to_string(), value);
        }
    }
    inherited
}

fn build_backend_launch_spec(layout: &RuntimeLayout) -> BackendLaunchSpec {
    let mut args = vec![OsString::from("run")];
    if layout.mode == RuntimeMode::Installed {
        args.push(OsString::from("--frozen"));
    }
    args.extend([
        OsString::from("--project"),
        layout.runtime_root.as_os_str().to_os_string(),
        OsString::from("python"),
    ]);
    if layout.mode == RuntimeMode::Installed {
        args.extend([OsString::from("-E"), OsString::from("-s")]);
    }
    args.extend([
        OsString::from("-u"),
        OsString::from("-m"),
        OsString::from("app.cli"),
        OsString::from("serve"),
        OsString::from("--desktop-loopback"),
        OsString::from("--port"),
        OsString::from(layout.backend_port.to_string()),
    ]);

    let mut command_env = if layout.mode == RuntimeMode::Installed {
        safe_inherited_environment(env::vars_os())
    } else {
        BTreeMap::new()
    };
    command_env.extend([
        ("PYTHONUTF8".to_string(), OsString::from("1")),
        ("PYTHONIOENCODING".to_string(), OsString::from("utf-8")),
        ("PYTHONUNBUFFERED".to_string(), OsString::from("1")),
        ("NO_COLOR".to_string(), OsString::from("1")),
        (
            "MPP_APP_VERSION".to_string(),
            OsString::from(env!("CARGO_PKG_VERSION")),
        ),
        (
            "MPP_DESKTOP_SESSION_TOKEN".to_string(),
            OsString::from(&layout.session_token),
        ),
        ("MPP_SKIP_VERSION_CHECK".to_string(), OsString::from("1")),
    ]);

    if layout.mode == RuntimeMode::Installed {
        command_env.extend([
            (
                "MPP_CONFIG_FILE".to_string(),
                path_env(&layout.user.config_file),
            ),
            ("MPP_LOG_DIR".to_string(), path_env(&layout.user.log_dir)),
            (
                "MPP_CACHE_DIR".to_string(),
                path_env(&layout.user.cache_dir),
            ),
            (
                "MPP_WEB_DIST_DIR".to_string(),
                path_env(&layout.web_dist_dir),
            ),
            ("MPP_DATA_ROOT".to_string(), path_env(&layout.user.data_dir)),
            (
                "UV_PROJECT_ENVIRONMENT".to_string(),
                path_env(&layout.user.venv_dir),
            ),
            (
                "UV_CACHE_DIR".to_string(),
                path_env(&layout.user.cache_dir.join("uv")),
            ),
            (
                "UV_PYTHON_INSTALL_DIR".to_string(),
                path_env(&layout.user.python_dir),
            ),
            ("UV_MANAGED_PYTHON".to_string(), OsString::from("1")),
            (
                "HF_HOME".to_string(),
                path_env(&layout.user.cache_dir.join("huggingface")),
            ),
            (
                "TORCH_HOME".to_string(),
                path_env(&layout.user.cache_dir.join("torch")),
            ),
            (
                "PLAYWRIGHT_BROWSERS_PATH".to_string(),
                path_env(&layout.user.cache_dir.join("ms-playwright")),
            ),
            ("PYTHONDONTWRITEBYTECODE".to_string(), OsString::from("1")),
            ("PYTHONNOUSERSITE".to_string(), OsString::from("1")),
            ("PYTHONSAFEPATH".to_string(), OsString::from("1")),
            ("TEMP".to_string(), path_env(&layout.user.temp_dir)),
            ("TMP".to_string(), path_env(&layout.user.temp_dir)),
            ("UV_LINK_MODE".to_string(), OsString::from("copy")),
            ("UV_NO_CONFIG".to_string(), OsString::from("1")),
        ]);
    }

    BackendLaunchSpec {
        program: layout.uv_executable.clone(),
        cwd: layout.backend_dir.clone(),
        args,
        env: command_env,
        clear_environment: layout.mode == RuntimeMode::Installed,
    }
}

const LOG_TRUNCATION_SUFFIX: &str = "… [truncated]";

fn bounded_log_line(raw_line: &str, force_truncated: bool) -> Option<String> {
    let line = raw_line.trim_end();
    let truncated = force_truncated || line.len() > MAX_LOG_LINE_BYTES;
    if line.is_empty() && !truncated {
        return None;
    }
    if !truncated {
        return Some(line.to_string());
    }

    let content_limit = MAX_LOG_LINE_BYTES.saturating_sub(LOG_TRUNCATION_SUFFIX.len());
    let mut end = line.len().min(content_limit);
    while end > 0 && !line.is_char_boundary(end) {
        end -= 1;
    }
    Some(format!("{}{}", &line[..end], LOG_TRUNCATION_SUFFIX))
}

fn append_log(app: &AppHandle, source: &str, text: impl AsRef<str>) {
    let backend = app.state::<BackendProcess>();
    for raw_line in text.as_ref().split(['\r', '\n']) {
        let Some(line) = bounded_log_line(raw_line, false) else {
            continue;
        };

        let entry = BackendLogEntry {
            ts: Utc::now().to_rfc3339(),
            source: source.to_string(),
            line,
        };

        if let Ok(mut logs) = backend.logs.lock() {
            logs.push(entry.clone());
            if logs.len() > MAX_LOG_LINES {
                let overflow = logs.len() - MAX_LOG_LINES;
                logs.drain(..overflow);
            }
        }

        let _ = app.emit("mpp-backend:log", &entry);
    }
}

fn set_status(
    app: &AppHandle,
    state_name: &str,
    pid: Option<u32>,
    message: impl Into<String>,
    cwd: Option<String>,
) -> Result<BackendStatus, String> {
    let phase = match state_name {
        "running" | "external" => "APP_READY",
        "starting" => "STARTING_BACKEND",
        "error" => "FAILED_RETRYABLE",
        _ => "READY_TO_START",
    };
    set_status_details(
        app, state_name, pid, message, cwd, phase, None, None, None, None,
    )
}

#[allow(clippy::too_many_arguments)]
fn set_status_details(
    app: &AppHandle,
    state_name: &str,
    pid: Option<u32>,
    message: impl Into<String>,
    cwd: Option<String>,
    phase: &str,
    error_code: Option<&str>,
    component_id: Option<&str>,
    remediation: Option<&str>,
    local_path: Option<String>,
) -> Result<BackendStatus, String> {
    let backend = app.state::<BackendProcess>();
    let mut status = backend
        .status
        .lock()
        .map_err(|_| "backend status lock poisoned".to_string())?;

    status.state = state_name.to_string();
    status.pid = pid;
    status.message = message.into();
    status.command = BACKEND_COMMAND.to_string();
    status.url = APP_URL.to_string();
    if let Some(cwd) = cwd {
        status.cwd = cwd;
    }
    status.phase = phase.to_string();
    status.error_code = error_code.map(str::to_string);
    status.component_id = component_id.map(str::to_string);
    status.remediation = remediation.map(str::to_string);
    status.local_path = local_path;

    let next = status.clone();
    let _ = app.emit("mpp-backend:status", &next);
    Ok(next)
}

fn set_bootstrap_phase(
    app: &AppHandle,
    phase: &str,
    message: impl Into<String>,
    local_path: Option<&Path>,
) -> Result<BackendStatus, String> {
    let current = current_status(app)?;
    let state = if phase == "APP_READY" {
        "running"
    } else if phase == "READY_TO_START" {
        "stopped"
    } else {
        "starting"
    };
    set_status_details(
        app,
        state,
        current.pid,
        message,
        None,
        phase,
        None,
        None,
        None,
        local_path.map(|path| path.to_string_lossy().into_owned()),
    )
}

fn set_bootstrap_failure(
    app: &AppHandle,
    failure: &BootstrapFailure,
) -> Result<BackendStatus, String> {
    append_log(
        app,
        "error",
        format!(
            "{} [{}]: {}",
            failure.error_code, failure.component_id, failure.detail
        ),
    );
    let pid = current_status(app)?.pid;
    set_status_details(
        app,
        "error",
        pid,
        &failure.detail,
        None,
        failure.phase(),
        Some(failure.error_code.as_str()),
        Some(failure.component_id.as_str()),
        Some(failure.remediation.as_str()),
        failure.local_path.clone(),
    )
}

fn current_status(app: &AppHandle) -> Result<BackendStatus, String> {
    let backend = app.state::<BackendProcess>();
    backend
        .status
        .lock()
        .map(|status| status.clone())
        .map_err(|_| "backend status lock poisoned".to_string())
}

fn health_request(
    nonce: &str,
    backend_port: u16,
    close_connection: bool,
) -> Result<Vec<u8>, String> {
    if !is_lower_hex(nonce, 64) {
        return Err(
            "desktop health nonce must be 32 random bytes encoded as lowercase hex".to_string(),
        );
    }
    let connection = if close_connection {
        "close"
    } else {
        "keep-alive"
    };
    Ok(format!(
        "GET /health HTTP/1.1\r\nHost: localhost:{backend_port}\r\n\
         X-MPP-Desktop-Nonce: {nonce}\r\nConnection: {connection}\r\n\r\n"
    )
    .into_bytes())
}

fn desktop_health_proof(session_secret: &str, nonce: &str) -> Result<String, String> {
    if !is_lower_hex(session_secret, 64) || !is_lower_hex(nonce, 64) {
        return Err("desktop health proof inputs must be lowercase 32-byte hex".to_string());
    }
    let message = format!(
        "{nonce}\0{HEALTH_PRODUCT}\0{HEALTH_PROTOCOL}\0{HEALTH_SERVICE}\0{}",
        env!("CARGO_PKG_VERSION")
    );
    let mut mac = Hmac::<Sha256>::new_from_slice(session_secret.as_bytes())
        .map_err(|_| "desktop session secret has an invalid HMAC length".to_string())?;
    mac.update(message.as_bytes());
    Ok(format!("{:x}", mac.finalize().into_bytes()))
}

fn classify_health_response(response: &[u8], session_secret: &str, nonce: &str) -> BackendProbe {
    let separator = response.windows(4).position(|window| window == b"\r\n\r\n");
    let Some(separator) = separator else {
        return BackendProbe::Occupied("port returned an invalid HTTP response".to_string());
    };
    let header_bytes = &response[..separator];
    let body = &response[separator + 4..];
    let Ok(headers) = std::str::from_utf8(header_bytes) else {
        return BackendProbe::Occupied("port returned non-UTF-8 HTTP headers".to_string());
    };
    let Some(status_line) = headers.lines().next() else {
        return BackendProbe::Occupied("port returned an empty HTTP response".to_string());
    };
    let mut status_parts = status_line.split_whitespace();
    let protocol = status_parts.next().unwrap_or_default();
    let status = status_parts
        .next()
        .and_then(|value| value.parse::<u16>().ok());
    if !matches!(protocol, "HTTP/1.0" | "HTTP/1.1") || status.is_none() {
        return BackendProbe::Occupied("port returned an invalid HTTP status line".to_string());
    }
    let status = status.expect("status was checked above");

    let value: serde_json::Value = match serde_json::from_slice(body) {
        Ok(value) => value,
        Err(error) => {
            return BackendProbe::Occupied(format!(
                "port did not return the MPP health JSON: {error}"
            ));
        }
    };
    let claims_mpp =
        value.get("product").and_then(serde_json::Value::as_str) == Some(HEALTH_PRODUCT);
    let payload: HealthPayload = match serde_json::from_value(value) {
        Ok(payload) => payload,
        Err(error) if claims_mpp => {
            return BackendProbe::Incompatible(format!(
                "MPP health response violates protocol {HEALTH_PROTOCOL}: {error}"
            ));
        }
        Err(error) => {
            return BackendProbe::Occupied(format!(
                "port returned unrelated JSON instead of MPP health data: {error}"
            ));
        }
    };
    if payload.product != HEALTH_PRODUCT || payload.service != HEALTH_SERVICE {
        return BackendProbe::Occupied(format!(
            "port belongs to an unexpected service: product={:?}, service={:?}",
            payload.product, payload.service
        ));
    }
    if status != 200 {
        return BackendProbe::Incompatible(format!("MPP health endpoint returned HTTP {status}"));
    }
    if payload.status != "healthy" {
        return BackendProbe::Incompatible(format!("MPP backend status is {:?}", payload.status));
    }
    if payload.protocol != HEALTH_PROTOCOL {
        return BackendProbe::Incompatible(format!(
            "MPP health protocol {} differs from desktop protocol {HEALTH_PROTOCOL}",
            payload.protocol
        ));
    }
    if payload.version != env!("CARGO_PKG_VERSION") {
        return BackendProbe::Incompatible(format!(
            "MPP backend version {} differs from desktop {}",
            payload.version,
            env!("CARGO_PKG_VERSION")
        ));
    }
    let expected_proof = match desktop_health_proof(session_secret, nonce) {
        Ok(proof) => proof,
        Err(error) => return BackendProbe::Incompatible(error),
    };
    if !payload.desktop_proof.as_deref().is_some_and(|proof| {
        is_lower_hex(proof, 64) && constant_time_token_match(&expected_proof, proof)
    }) {
        return BackendProbe::Incompatible(
            "MPP backend desktop health proof is missing or invalid".to_string(),
        );
    }
    BackendProbe::Compatible
}

fn probe_backend_at_with_timeouts(
    addr: SocketAddr,
    session_secret: &str,
    connect_timeout: Duration,
    health_timeout: Duration,
) -> BackendProbe {
    let mut stream = match TcpStream::connect_timeout(&addr, connect_timeout) {
        Ok(stream) => stream,
        Err(_) => return BackendProbe::Unavailable,
    };

    let nonce = match generate_session_token() {
        Ok(nonce) => nonce,
        Err(error) => return BackendProbe::Incompatible(error),
    };
    let request = match health_request(&nonce, addr.port(), true) {
        Ok(request) => request,
        Err(error) => return BackendProbe::Incompatible(error),
    };
    if stream.write_all(&request).is_err() {
        return BackendProbe::Unavailable;
    }

    match read_health_response_until(&mut stream, false, Instant::now() + health_timeout) {
        Ok(response) => classify_health_response(&response, session_secret, &nonce),
        Err(error) if error.transient => BackendProbe::Unavailable,
        Err(error) => BackendProbe::Occupied(format!(
            "port accepted a connection but returned an invalid health response: {error}"
        )),
    }
}

fn probe_backend_at(addr: SocketAddr, session_secret: &str) -> BackendProbe {
    probe_backend_at_with_timeouts(
        addr,
        session_secret,
        Duration::from_millis(350),
        Duration::from_millis(800),
    )
}

fn probe_backend(session_token: &str, backend_port: u16) -> BackendProbe {
    let ipv4 = probe_backend_at(
        SocketAddr::from(([127, 0, 0, 1], backend_port)),
        session_token,
    );
    let ipv6 = probe_backend_at(
        SocketAddr::from(([0, 0, 0, 0, 0, 0, 0, 1], backend_port)),
        session_token,
    );
    combine_loopback_probes_with_policy(ipv4, ipv6, ipv6_loopback_supported())
}

#[cfg(test)]
fn combine_loopback_probes(ipv4: BackendProbe, ipv6: BackendProbe) -> BackendProbe {
    combine_loopback_probes_with_policy(ipv4, ipv6, true)
}

fn combine_loopback_probes_with_policy(
    ipv4: BackendProbe,
    ipv6: BackendProbe,
    require_ipv6: bool,
) -> BackendProbe {
    for (label, probe) in [("IPv4 localhost", &ipv4), ("IPv6 localhost", &ipv6)] {
        if label == "IPv6 localhost" && !require_ipv6 {
            continue;
        }
        if let BackendProbe::Occupied(reason) = probe {
            return BackendProbe::Occupied(format!("{label}: {reason}"));
        }
    }
    for (label, probe) in [("IPv4 localhost", &ipv4), ("IPv6 localhost", &ipv6)] {
        if label == "IPv6 localhost" && !require_ipv6 {
            continue;
        }
        if let BackendProbe::Incompatible(reason) = probe {
            return BackendProbe::Incompatible(format!("{label}: {reason}"));
        }
    }
    if ipv4 == BackendProbe::Compatible && (!require_ipv6 || ipv6 == BackendProbe::Compatible) {
        BackendProbe::Compatible
    } else {
        BackendProbe::Unavailable
    }
}

fn ipv6_loopback_supported() -> bool {
    match bind_proxy_listener(SocketAddr::from((Ipv6Addr::LOCALHOST, 0))) {
        Ok(listener) => {
            drop(listener);
            true
        }
        Err(error) => !ipv6_listener_is_unavailable(&error),
    }
}

#[cfg(windows)]
fn set_exclusive_address_use(socket: &Socket) -> io::Result<()> {
    use std::os::windows::io::AsRawSocket;
    use windows_sys::Win32::Networking::WinSock::{
        setsockopt, SOCKET_ERROR, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
    };

    let enabled = 1_i32;
    let result = unsafe {
        setsockopt(
            socket.as_raw_socket() as usize,
            SOL_SOCKET,
            SO_EXCLUSIVEADDRUSE,
            (&enabled as *const i32).cast(),
            std::mem::size_of::<i32>() as i32,
        )
    };
    if result == SOCKET_ERROR {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn set_exclusive_address_use(socket: &Socket) -> io::Result<()> {
    socket.set_reuse_address(false)
}

fn bind_proxy_listener(address: SocketAddr) -> io::Result<TcpListener> {
    let domain = match address.ip() {
        IpAddr::V4(_) => Domain::IPV4,
        IpAddr::V6(_) => Domain::IPV6,
    };
    let socket = Socket::new(domain, Type::STREAM, Some(Protocol::TCP))?;
    set_exclusive_address_use(&socket)?;
    if address.is_ipv6() {
        socket.set_only_v6(true)?;
    }
    socket.bind(&address.into())?;
    socket.listen(MAX_PROXY_CONNECTIONS as i32)?;
    Ok(socket.into())
}

fn ipv6_listener_is_unavailable(error: &io::Error) -> bool {
    if matches!(
        error.kind(),
        io::ErrorKind::AddrNotAvailable | io::ErrorKind::Unsupported
    ) {
        return true;
    }
    #[cfg(windows)]
    {
        matches!(
            error.raw_os_error(),
            Some(10043) | Some(10047) | Some(10049)
        )
    }
    #[cfg(not(windows))]
    {
        matches!(error.raw_os_error(), Some(93) | Some(97) | Some(99))
    }
}

fn bind_desktop_proxy_at(port: u16) -> Result<ProxyListeners, String> {
    let ipv4_address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let ipv4 = bind_proxy_listener(ipv4_address)
        .map_err(|error| format!("failed to bind the desktop proxy at {ipv4_address}: {error}"))?;
    let ipv6 = match bind_proxy_listener(SocketAddr::from((Ipv6Addr::LOCALHOST, port))) {
        Ok(listener) => Some(listener),
        Err(error) if ipv6_listener_is_unavailable(&error) => None,
        Err(error) => {
            drop(ipv4);
            return Err(format!(
                "desktop proxy IPv6 loopback ownership failed atomically: {error}"
            ));
        }
    };
    Ok(ProxyListeners { ipv4, ipv6 })
}

fn remaining_deadline(deadline: Instant) -> Result<Duration, HttpReadFailure> {
    let remaining = deadline
        .checked_duration_since(Instant::now())
        .ok_or_else(|| HttpReadFailure::transient("HTTP deadline expired"))?;
    Ok(remaining.max(Duration::from_millis(1)))
}

fn read_http_head_until(
    stream: &mut TcpStream,
    deadline: Instant,
) -> Result<(Vec<u8>, usize), HttpReadFailure> {
    let mut message = Vec::with_capacity(4096);
    let mut buffer = [0_u8; 4096];
    loop {
        if let Some(separator) = message.windows(4).position(|window| window == b"\r\n\r\n") {
            return Ok((message, separator + 4));
        }
        if message.len() >= MAX_HTTP_HEAD_BYTES {
            return Err(HttpReadFailure::invalid(
                "HTTP header section exceeds the desktop proxy limit",
            ));
        }
        let available = (MAX_HTTP_HEAD_BYTES - message.len()).min(buffer.len());
        stream
            .set_read_timeout(Some(remaining_deadline(deadline)?))
            .map_err(|error| {
                HttpReadFailure::transient(format!(
                    "failed to apply the HTTP header deadline: {error}"
                ))
            })?;
        let read = stream.read(&mut buffer[..available]).map_err(|error| {
            HttpReadFailure::transient(format!("failed to read HTTP headers: {error}"))
        })?;
        if read == 0 {
            return Err(HttpReadFailure::transient(
                "connection closed before the HTTP headers completed",
            ));
        }
        message.extend_from_slice(&buffer[..read]);
    }
}

fn read_exact_until(
    stream: &mut TcpStream,
    mut output: &mut [u8],
    deadline: Instant,
) -> Result<(), HttpReadFailure> {
    while !output.is_empty() {
        stream
            .set_read_timeout(Some(remaining_deadline(deadline)?))
            .map_err(|error| {
                HttpReadFailure::transient(format!(
                    "failed to apply the HTTP body deadline: {error}"
                ))
            })?;
        let read = stream.read(output).map_err(|error| {
            HttpReadFailure::transient(format!("failed to read the HTTP body: {error}"))
        })?;
        if read == 0 {
            return Err(HttpReadFailure::transient(
                "connection closed before the HTTP body completed",
            ));
        }
        output = &mut output[read..];
    }
    Ok(())
}

fn parse_http_headers(lines: &[&str]) -> Result<Vec<ParsedHttpHeader>, String> {
    if lines.len() > 128 {
        return Err("HTTP message contains too many headers".to_string());
    }
    lines
        .iter()
        .map(|line| {
            if line.starts_with([' ', '\t']) {
                return Err("folded HTTP headers are not accepted".to_string());
            }
            let (name, value) = line
                .split_once(':')
                .ok_or_else(|| "HTTP header is missing a colon".to_string())?;
            if !is_http_token(name) {
                return Err(format!("HTTP header name {name:?} is invalid"));
            }
            let value = value.trim_matches([' ', '\t']);
            if value
                .chars()
                .any(|character| character.is_control() && character != '\t')
            {
                return Err(format!("HTTP header {name:?} contains control characters"));
            }
            Ok(ParsedHttpHeader {
                name: name.to_string(),
                value: value.to_string(),
            })
        })
        .collect()
}

fn split_http_head(head: &[u8]) -> Result<Vec<&str>, String> {
    if !head.ends_with(b"\r\n\r\n") {
        return Err("HTTP header section is incomplete".to_string());
    }
    let text =
        std::str::from_utf8(&head[..head.len() - 4]).map_err(|_| "HTTP headers are not UTF-8")?;
    let lines = text.split("\r\n").collect::<Vec<_>>();
    if lines.is_empty() || lines.iter().any(|line| line.is_empty()) {
        return Err("HTTP header section contains an unexpected empty line".to_string());
    }
    Ok(lines)
}

fn parse_http_request(head: &[u8]) -> Result<ParsedHttpRequest, String> {
    let lines = split_http_head(head)?;
    let request_line = lines[0].split_ascii_whitespace().collect::<Vec<_>>();
    if request_line.len() != 3 || request_line[2] != "HTTP/1.1" || !is_http_token(request_line[0]) {
        return Err("desktop proxy accepts valid HTTP/1.1 request lines".to_string());
    }
    Ok(ParsedHttpRequest {
        method: request_line[0].to_string(),
        path: request_line[1].to_string(),
        headers: parse_http_headers(&lines[1..])?,
    })
}

fn parse_http_response(head: &[u8]) -> Result<ParsedHttpResponse, String> {
    let lines = split_http_head(head)?;
    let status_line = lines[0].split_ascii_whitespace().collect::<Vec<_>>();
    if status_line.len() < 2 || status_line[0] != "HTTP/1.1" {
        return Err("backend response is not HTTP/1.1".to_string());
    }
    let status = status_line[1]
        .parse::<u16>()
        .map_err(|_| "backend HTTP status is invalid".to_string())?;
    if !(100..=599).contains(&status) {
        return Err("backend HTTP status is outside the valid range".to_string());
    }
    Ok(ParsedHttpResponse {
        status,
        headers: parse_http_headers(&lines[1..])?,
    })
}

fn single_header_value(
    headers: &[ParsedHttpHeader],
    name: &str,
) -> Result<Option<String>, ProxyFailure> {
    let mut values = headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case(name))
        .map(|header| header.value.trim().to_string());
    let first = values.next();
    if values.next().is_some() {
        return Err(ProxyFailure::new(
            ProxyFailureKind::BadRequest,
            format!("duplicate {name} headers are not accepted"),
        ));
    }
    Ok(first)
}

fn parse_response_content_length(headers: &[ParsedHttpHeader]) -> Result<usize, String> {
    if headers
        .iter()
        .any(|header| header.name.eq_ignore_ascii_case("transfer-encoding"))
    {
        return Err("health response cannot use transfer encoding".to_string());
    }
    let lengths = headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case("content-length"))
        .collect::<Vec<_>>();
    if lengths.len() != 1 {
        return Err("health response must contain one Content-Length header".to_string());
    }
    let value = lengths[0]
        .value
        .trim()
        .parse::<usize>()
        .map_err(|_| "health response Content-Length is invalid".to_string())?;
    if value > MAX_HEALTH_RESPONSE_BYTES {
        return Err("health response body exceeds the desktop proxy limit".to_string());
    }
    Ok(value)
}

fn read_health_response(
    stream: &mut TcpStream,
    require_keep_alive: bool,
) -> Result<Vec<u8>, HttpReadFailure> {
    read_health_response_until(
        stream,
        require_keep_alive,
        Instant::now() + PROXY_BACKEND_HEALTH_TIMEOUT,
    )
}

fn read_health_response_until(
    stream: &mut TcpStream,
    require_keep_alive: bool,
    deadline: Instant,
) -> Result<Vec<u8>, HttpReadFailure> {
    let (mut response, head_end) = read_http_head_until(stream, deadline)?;
    let parsed = parse_http_response(&response[..head_end]).map_err(|error| {
        HttpReadFailure::invalid(format!("health response headers are invalid: {error}"))
    })?;
    let body_length =
        parse_response_content_length(&parsed.headers).map_err(HttpReadFailure::invalid)?;
    if require_keep_alive {
        let closes = parsed
            .headers
            .iter()
            .filter(|header| header.name.eq_ignore_ascii_case("connection"))
            .flat_map(|header| header.value.split(','))
            .any(|token| token.trim().eq_ignore_ascii_case("close"));
        if closes {
            return Err(HttpReadFailure::invalid(
                "backend closed the authenticated health connection",
            ));
        }
    }
    let total_length = head_end
        .checked_add(body_length)
        .ok_or_else(|| HttpReadFailure::invalid("health response length overflowed"))?;
    if response.len() > total_length {
        return Err(HttpReadFailure::invalid(
            "backend sent unexpected bytes after its health response",
        ));
    }
    let received_length = response.len();
    response.resize(total_length, 0);
    read_exact_until(stream, &mut response[received_length..], deadline).map_err(|error| {
        HttpReadFailure {
            transient: error.transient,
            message: format!("health response body was incomplete: {error}"),
        }
    })?;
    Ok(response)
}

fn authenticate_backend_connection(
    stream: &mut TcpStream,
    session_secret: &str,
    backend_port: u16,
) -> Result<(), String> {
    // This challenge runs on the exact TCP stream that will receive the user
    // request, closing the port-replacement window before credentials or body
    // bytes leave the Rust process.
    stream
        .set_read_timeout(Some(PROXY_BACKEND_HEALTH_TIMEOUT))
        .map_err(|error| format!("failed to set backend health read deadline: {error}"))?;
    stream
        .set_write_timeout(Some(PROXY_BACKEND_HEALTH_TIMEOUT))
        .map_err(|error| format!("failed to set backend health write deadline: {error}"))?;
    let nonce = generate_session_token()?;
    let request = health_request(&nonce, backend_port, false)?;
    stream
        .write_all(&request)
        .map_err(|error| format!("failed to send the private backend health challenge: {error}"))?;
    let response = read_health_response(stream, true).map_err(|error| error.to_string())?;
    match classify_health_response(&response, session_secret, &nonce) {
        BackendProbe::Compatible => Ok(()),
        BackendProbe::Unavailable => Err("private backend became unavailable".to_string()),
        BackendProbe::Incompatible(reason) | BackendProbe::Occupied(reason) => Err(reason),
    }
}

fn connect_authenticated_backend(layout: &RuntimeLayout) -> Result<TcpStream, String> {
    let mut failures = Vec::new();
    for address in [
        SocketAddr::from((Ipv4Addr::LOCALHOST, layout.backend_port)),
        SocketAddr::from((Ipv6Addr::LOCALHOST, layout.backend_port)),
    ] {
        let mut stream = match TcpStream::connect_timeout(&address, PROXY_BACKEND_CONNECT_TIMEOUT) {
            Ok(stream) => stream,
            Err(error) => {
                failures.push(format!("{address}: {error}"));
                continue;
            }
        };
        stream
            .set_nodelay(true)
            .map_err(|error| format!("failed to configure the backend connection: {error}"))?;
        authenticate_backend_connection(&mut stream, &layout.session_token, layout.backend_port)?;
        stream
            .set_read_timeout(None)
            .map_err(|error| format!("failed to clear the backend read deadline: {error}"))?;
        stream
            .set_write_timeout(None)
            .map_err(|error| format!("failed to clear the backend write deadline: {error}"))?;
        return Ok(stream);
    }
    Err(format!(
        "private backend is unavailable on both loopback families: {}",
        failures.join("; ")
    ))
}

fn request_private_backend_shutdown(layout: &RuntimeLayout) -> Result<(), String> {
    let mut stream = connect_authenticated_backend(layout)?;
    stream
        .set_read_timeout(Some(PROXY_BACKEND_HEALTH_TIMEOUT))
        .map_err(|error| format!("failed to set shutdown response deadline: {error}"))?;
    stream
        .set_write_timeout(Some(PROXY_BACKEND_HEALTH_TIMEOUT))
        .map_err(|error| format!("failed to set shutdown request deadline: {error}"))?;
    let request = format!(
        "POST /api/desktop/shutdown HTTP/1.1\r\nHost: localhost:{}\r\n\
         X-MPP-Desktop-Session: {}\r\nX-Requested-With: fetch\r\n\
         Content-Length: 0\r\nConnection: close\r\n\r\n",
        layout.backend_port, layout.session_token
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("failed to send the private backend shutdown request: {error}"))?;
    let response = read_health_response(&mut stream, false).map_err(|error| error.to_string())?;
    let head_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|index| index + 4)
        .ok_or_else(|| "shutdown response has no header terminator".to_string())?;
    let status = parse_http_response(&response[..head_end])
        .map_err(|error| format!("shutdown response is invalid HTTP: {error}"))?
        .status;
    if status == 200 {
        Ok(())
    } else {
        Err(format!(
            "private backend shutdown endpoint returned HTTP {status}"
        ))
    }
}

fn trusted_cors_origin(origin: &str) -> bool {
    matches!(origin, "http://tauri.localhost" | "tauri://localhost")
}

fn constant_time_token_match(expected: &str, candidate: &str) -> bool {
    const COMPARISON_KEY: &[u8] = b"mpp-desktop-constant-time-token-comparison-v1";
    let mut expected_mac =
        Hmac::<Sha256>::new_from_slice(COMPARISON_KEY).expect("comparison key is valid");
    expected_mac.update(expected.as_bytes());
    let expected_tag = expected_mac.finalize().into_bytes();
    let mut candidate_mac =
        Hmac::<Sha256>::new_from_slice(COMPARISON_KEY).expect("comparison key is valid");
    candidate_mac.update(candidate.as_bytes());
    candidate_mac.verify_slice(&expected_tag).is_ok()
}

fn extract_proxy_cookie(
    headers: &[ParsedHttpHeader],
    expected_token: Option<&str>,
) -> Result<Vec<String>, ProxyFailure> {
    let mut supplied_token = None;
    let mut forwarded = Vec::new();
    for header in headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case("cookie"))
    {
        let value = header.value.trim();
        for pair in value.split(';') {
            let pair = pair.trim();
            let Some((name, value)) = pair.split_once('=') else {
                return Err(ProxyFailure::new(
                    ProxyFailureKind::BadRequest,
                    "Cookie header contains a malformed pair",
                ));
            };
            if name.trim() == DESKTOP_PROXY_COOKIE {
                if supplied_token.replace(value.trim().to_string()).is_some() {
                    return Err(ProxyFailure::new(
                        ProxyFailureKind::Unauthorized,
                        "desktop proxy cookie was supplied more than once",
                    ));
                }
            } else {
                forwarded.push(pair.to_string());
            }
        }
    }
    if let Some(expected_token) = expected_token {
        let supplied_token = supplied_token.ok_or_else(|| {
            ProxyFailure::new(
                ProxyFailureKind::Unauthorized,
                "desktop proxy cookie is missing",
            )
        })?;
        if !constant_time_token_match(expected_token, &supplied_token) {
            return Err(ProxyFailure::new(
                ProxyFailureKind::Unauthorized,
                "desktop proxy cookie is invalid",
            ));
        }
    }
    Ok(forwarded)
}

fn public_proxy_host(host: &str) -> bool {
    [
        format!("localhost:{BACKEND_PORT}"),
        format!("127.0.0.1:{BACKEND_PORT}"),
        format!("[::1]:{BACKEND_PORT}"),
    ]
    .iter()
    .any(|expected| host.eq_ignore_ascii_case(expected))
}

fn public_browser_origin_matches_host(origin: &str, host: &str) -> bool {
    url::Url::parse(origin).is_ok_and(|origin_url| {
        origin_url.scheme() == "http"
            && origin_url.username().is_empty()
            && origin_url.password().is_none()
            && origin_url.port() == Some(BACKEND_PORT)
            && origin_url.path() == "/"
            && origin_url.query().is_none()
            && origin_url.fragment().is_none()
            && origin_url.host().is_some_and(|origin_host| {
                let expected = match origin_host {
                    url::Host::Ipv6(address) => format!("[{address}]:{BACKEND_PORT}"),
                    url::Host::Ipv4(address) => format!("{address}:{BACKEND_PORT}"),
                    url::Host::Domain(domain) => format!("{domain}:{BACKEND_PORT}"),
                };
                host.eq_ignore_ascii_case(&expected)
            })
    })
}

fn validate_public_browser_context(
    headers: &[ParsedHttpHeader],
    host: &str,
    origin: Option<&str>,
) -> Result<(), ProxyFailure> {
    if let Some(origin) = origin {
        if !public_browser_origin_matches_host(origin, host) {
            return Err(ProxyFailure::new(
                ProxyFailureKind::Forbidden,
                format!("browser Origin {origin:?} does not match public proxy Host {host:?}"),
            ));
        }
    }
    let sec_fetch_values = headers
        .iter()
        .filter(|header| header.name.to_ascii_lowercase().starts_with("sec-fetch-"))
        .collect::<Vec<_>>();
    if sec_fetch_values.is_empty() {
        return Ok(());
    }
    let site = single_header_value(headers, "Sec-Fetch-Site")?.ok_or_else(|| {
        ProxyFailure::new(
            ProxyFailureKind::Forbidden,
            "browser request is missing Sec-Fetch-Site",
        )
    })?;
    if !matches!(site.as_str(), "same-origin" | "none") {
        return Err(ProxyFailure::new(
            ProxyFailureKind::Forbidden,
            format!("browser Sec-Fetch-Site {site:?} is not trusted"),
        ));
    }
    if let Some(referer) = single_header_value(headers, "Referer")? {
        let referer_url = url::Url::parse(&referer).map_err(|_| {
            ProxyFailure::new(ProxyFailureKind::Forbidden, "browser Referer is invalid")
        })?;
        let referer_origin = referer_url.origin().ascii_serialization();
        if !public_browser_origin_matches_host(&referer_origin, host) {
            return Err(ProxyFailure::new(
                ProxyFailureKind::Forbidden,
                "browser Referer does not match the public proxy Host",
            ));
        }
    }
    Ok(())
}

fn is_http_token(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(
                    byte,
                    b'!' | b'#'
                        | b'$'
                        | b'%'
                        | b'&'
                        | b'\''
                        | b'*'
                        | b'+'
                        | b'-'
                        | b'.'
                        | b'^'
                        | b'_'
                        | b'`'
                        | b'|'
                        | b'~'
                )
        })
}

fn parse_request_body_framing(
    headers: &[ParsedHttpHeader],
) -> Result<RequestBodyFraming, ProxyFailure> {
    let content_lengths = headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case("content-length"))
        .collect::<Vec<_>>();
    let transfer_encodings = headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case("transfer-encoding"))
        .collect::<Vec<_>>();
    if content_lengths.len() > 1 || transfer_encodings.len() > 1 {
        return Err(ProxyFailure::new(
            ProxyFailureKind::BadRequest,
            "ambiguous HTTP request framing",
        ));
    }
    if !content_lengths.is_empty() && !transfer_encodings.is_empty() {
        return Err(ProxyFailure::new(
            ProxyFailureKind::BadRequest,
            "Content-Length and Transfer-Encoding cannot be combined",
        ));
    }
    if let Some(header) = content_lengths.first() {
        let value = header.value.trim().to_string();
        if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "Content-Length is invalid",
            ));
        }
        let length = value.parse::<u64>().map_err(|_| {
            ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "Content-Length exceeds the supported range",
            )
        })?;
        return Ok(if length == 0 {
            RequestBodyFraming::None
        } else {
            RequestBodyFraming::ContentLength(length)
        });
    }
    if let Some(header) = transfer_encodings.first() {
        let value = header.value.trim().to_string();
        if !value.eq_ignore_ascii_case("chunked") {
            return Err(ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "only chunked request transfer encoding is supported",
            ));
        }
        return Ok(RequestBodyFraming::Chunked);
    }
    Ok(RequestBodyFraming::None)
}

fn cors_preflight_response(
    origin: &str,
    requested_headers: Option<&str>,
) -> Result<Vec<u8>, ProxyFailure> {
    let allowed_headers = [
        "accept",
        "authorization",
        "cache-control",
        "content-type",
        "if-modified-since",
        "last-event-id",
        "pragma",
        "range",
        "x-requested-with",
    ];
    let mut normalized = Vec::new();
    if let Some(requested_headers) = requested_headers {
        for header in requested_headers.split(',') {
            let header = header.trim().to_ascii_lowercase();
            if !is_http_token(&header) || !allowed_headers.contains(&header.as_str()) {
                return Err(ProxyFailure::new(
                    ProxyFailureKind::Forbidden,
                    format!("CORS request header {header:?} is not allowed"),
                )
                .with_cors_origin(Some(origin)));
            }
            if !normalized.contains(&header) {
                normalized.push(header);
            }
        }
    }
    let allow_headers = if normalized.is_empty() {
        String::new()
    } else {
        format!(
            "Access-Control-Allow-Headers: {}\r\n",
            normalized.join(", ")
        )
    };
    Ok(format!(
        "HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: {origin}\r\n\
         Access-Control-Allow-Credentials: true\r\n\
         Access-Control-Allow-Methods: GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS\r\n\
         {allow_headers}Access-Control-Max-Age: 600\r\nVary: Origin\r\n\
         Cache-Control: no-store\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )
    .into_bytes())
}

fn prepare_proxy_request(
    message: Vec<u8>,
    head_end: usize,
    layout: &RuntimeLayout,
) -> Result<ProxyRequestAction, ProxyFailure> {
    let parsed = parse_http_request(&message[..head_end]).map_err(|error| {
        ProxyFailure::new(
            ProxyFailureKind::BadRequest,
            format!("HTTP request headers are invalid: {error}"),
        )
    })?;
    let method = parsed.method.as_str();
    let path = parsed.path.as_str();
    if !path.starts_with('/') || path.starts_with("//") || path.contains('\\') {
        return Err(ProxyFailure::new(
            ProxyFailureKind::BadRequest,
            "HTTP request target must use a local origin-form path",
        ));
    }
    if !matches!(
        method,
        "GET" | "HEAD" | "POST" | "PUT" | "PATCH" | "DELETE" | "OPTIONS"
    ) {
        return Err(ProxyFailure::new(
            ProxyFailureKind::MethodNotAllowed,
            format!("HTTP method {method:?} is not allowed"),
        ));
    }
    let host = single_header_value(&parsed.headers, "Host")?
        .ok_or_else(|| ProxyFailure::new(ProxyFailureKind::BadRequest, "Host header is missing"))?;
    let expected_host = format!("{DESKTOP_API_HOST}:{BACKEND_PORT}");
    // The packaged WebView receives a desktop-session upgrade only after its
    // HttpOnly cookie passes. Public localhost clients retain the normal
    // bearer/cookie and CSRF contract used by the browser UI and CLI.
    let trusted_webview = host.eq_ignore_ascii_case(&expected_host);
    if !trusted_webview && !public_proxy_host(&host) {
        return Err(ProxyFailure::new(
            ProxyFailureKind::Forbidden,
            format!("unexpected desktop proxy Host header {host:?}"),
        ));
    }
    let origin = single_header_value(&parsed.headers, "Origin")?;
    if trusted_webview {
        if origin
            .as_deref()
            .is_some_and(|value| !trusted_cors_origin(value))
        {
            return Err(ProxyFailure::new(
                ProxyFailureKind::Forbidden,
                format!("untrusted desktop proxy Origin header {origin:?}"),
            ));
        }
    } else {
        validate_public_browser_context(&parsed.headers, &host, origin.as_deref())?;
    }
    let body_framing = parse_request_body_framing(&parsed.headers)
        .map_err(|failure| failure.with_cors_origin(origin.as_deref()))?;
    let body_prefix = message[head_end..].to_vec();
    match body_framing {
        RequestBodyFraming::None if !body_prefix.is_empty() => {
            return Err(ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "request contains bytes outside its declared body",
            )
            .with_cors_origin(origin.as_deref()))
        }
        RequestBodyFraming::ContentLength(length) if body_prefix.len() as u64 > length => {
            return Err(ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "request body prefix exceeds Content-Length",
            )
            .with_cors_origin(origin.as_deref()))
        }
        _ => {}
    }

    let access_control_request_method =
        single_header_value(&parsed.headers, "Access-Control-Request-Method")?;
    if trusted_webview && method == "OPTIONS" {
        let requested_method = access_control_request_method.ok_or_else(|| {
            ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "CORS preflight method is missing",
            )
            .with_cors_origin(origin.as_deref())
        })?;
        let origin = origin.as_deref().ok_or_else(|| {
            ProxyFailure::new(
                ProxyFailureKind::Forbidden,
                "CORS preflight origin is missing",
            )
        })?;
        if !matches!(
            requested_method.as_str(),
            "GET" | "HEAD" | "POST" | "PUT" | "PATCH" | "DELETE"
        ) {
            return Err(ProxyFailure::new(
                ProxyFailureKind::MethodNotAllowed,
                format!("preflight method {requested_method:?} is not allowed"),
            )
            .with_cors_origin(Some(origin)));
        }
        if body_framing != RequestBodyFraming::None {
            return Err(ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                "CORS preflight cannot contain a request body",
            )
            .with_cors_origin(Some(origin)));
        }
        let requested_headers =
            single_header_value(&parsed.headers, "Access-Control-Request-Headers")?;
        return cors_preflight_response(origin, requested_headers.as_deref())
            .map(ProxyRequestAction::Preflight);
    }

    let forwarded_cookies = extract_proxy_cookie(
        &parsed.headers,
        trusted_webview.then_some(layout.proxy_token.as_str()),
    )
    .map_err(|failure| failure.with_cors_origin(origin.as_deref()))?;
    let connection_tokens = parsed
        .headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case("connection"))
        .map(|header| header.value.trim().to_string())
        .collect::<Vec<_>>()
        .into_iter()
        .flat_map(|value| {
            value
                .split(',')
                .map(str::trim)
                .map(str::to_ascii_lowercase)
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    if connection_tokens
        .iter()
        .any(|token| !matches!(token.as_str(), "" | "close" | "keep-alive" | "upgrade"))
    {
        return Err(ProxyFailure::new(
            ProxyFailureKind::BadRequest,
            "Connection header nominates unsupported hop-by-hop headers",
        )
        .with_cors_origin(origin.as_deref()));
    }
    let mut rewritten = format!("{method} {path} HTTP/1.1\r\nHost: {host}\r\n").into_bytes();
    for header in parsed.headers {
        let lower_name = header.name.to_ascii_lowercase();
        if matches!(
            lower_name.as_str(),
            "host"
                | "cookie"
                | "connection"
                | "proxy-connection"
                | "proxy-authorization"
                | "keep-alive"
                | "upgrade"
                | "te"
                | "x-mpp-desktop-session"
                | "x-mpp-desktop-nonce"
                | "forwarded"
                | "x-forwarded-for"
                | "x-forwarded-host"
                | "x-forwarded-proto"
        ) {
            continue;
        }
        rewritten.extend_from_slice(header.name.as_bytes());
        rewritten.extend_from_slice(b": ");
        rewritten.extend_from_slice(header.value.as_bytes());
        rewritten.extend_from_slice(b"\r\n");
    }
    if !forwarded_cookies.is_empty() {
        rewritten.extend_from_slice(b"Cookie: ");
        rewritten.extend_from_slice(forwarded_cookies.join("; ").as_bytes());
        rewritten.extend_from_slice(b"\r\n");
    }
    if trusted_webview {
        rewritten.extend_from_slice(b"X-MPP-Desktop-Session: ");
        rewritten.extend_from_slice(layout.session_token.as_bytes());
        rewritten.extend_from_slice(b"\r\n");
    }
    rewritten.extend_from_slice(b"Connection: close\r\n\r\n");
    Ok(ProxyRequestAction::Forward(PreparedProxyRequest {
        head: rewritten,
        body_prefix,
        body_framing,
        cors_origin: origin
            .as_deref()
            .filter(|value| trusted_cors_origin(value))
            .map(str::to_string),
    }))
}

fn proxy_failure_response(failure: &ProxyFailure) -> Vec<u8> {
    let (status, reason, message) = match failure.kind {
        ProxyFailureKind::BadRequest => (400, "Bad Request", "Bad Request"),
        ProxyFailureKind::Unauthorized => (401, "Unauthorized", "Unauthorized"),
        ProxyFailureKind::Forbidden => (403, "Forbidden", "Forbidden"),
        ProxyFailureKind::MethodNotAllowed => (405, "Method Not Allowed", "Method Not Allowed"),
        ProxyFailureKind::TooManyRequests => (429, "Too Many Requests", "Too Many Requests"),
        ProxyFailureKind::BadGateway => (502, "Bad Gateway", "Bad Gateway"),
        ProxyFailureKind::ServiceUnavailable => (503, "Service Unavailable", "Service Unavailable"),
    };
    let cors = failure
        .cors_origin
        .as_deref()
        .filter(|origin| trusted_cors_origin(origin))
        .map(|origin| {
            format!(
                "Access-Control-Allow-Origin: {origin}\r\n\
                 Access-Control-Allow-Credentials: true\r\nVary: Origin\r\n"
            )
        })
        .unwrap_or_default();
    format!(
        "HTTP/1.1 {status} {reason}\r\n{cors}Content-Type: text/plain; charset=utf-8\r\n\
         X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{message}",
        message.len()
    )
    .into_bytes()
}

struct PrefixReader {
    prefix: Vec<u8>,
    position: usize,
    stream: TcpStream,
}

impl PrefixReader {
    fn new(prefix: Vec<u8>, stream: TcpStream) -> Self {
        Self {
            prefix,
            position: 0,
            stream,
        }
    }

    fn prefix_remaining(&self) -> usize {
        self.prefix.len().saturating_sub(self.position)
    }

    fn read_until_deadline(
        &mut self,
        buffer: &mut [u8],
        deadline: Instant,
    ) -> Result<usize, String> {
        if self.position < self.prefix.len() {
            return self
                .read(buffer)
                .map_err(|error| format!("failed to read buffered request bytes: {error}"));
        }
        self.stream
            .set_read_timeout(Some(
                remaining_deadline(deadline).map_err(|error| error.to_string())?,
            ))
            .map_err(|error| format!("failed to apply the body framing deadline: {error}"))?;
        self.stream
            .read(buffer)
            .map_err(|error| format!("failed to read request body framing: {error}"))
    }

    fn restore_body_idle_timeout(&self) -> Result<(), String> {
        self.stream
            .set_read_timeout(Some(PROXY_CLIENT_BODY_IDLE_TIMEOUT))
            .map_err(|error| format!("failed to restore the request body idle timeout: {error}"))
    }
}

impl Read for PrefixReader {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if self.position < self.prefix.len() {
            let read = buffer.len().min(self.prefix.len() - self.position);
            buffer[..read].copy_from_slice(&self.prefix[self.position..self.position + read]);
            self.position += read;
            Ok(read)
        } else {
            self.stream.read(buffer)
        }
    }
}

fn copy_exact_bytes(
    reader: &mut PrefixReader,
    writer: &mut TcpStream,
    mut remaining: u64,
) -> Result<(), String> {
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        let requested = remaining.min(buffer.len() as u64) as usize;
        let read = reader
            .read(&mut buffer[..requested])
            .map_err(|error| format!("failed to read request body: {error}"))?;
        if read == 0 {
            return Err("request body ended before Content-Length bytes arrived".to_string());
        }
        writer
            .write_all(&buffer[..read])
            .map_err(|error| format!("failed to forward request body: {error}"))?;
        remaining -= read as u64;
    }
    Ok(())
}

fn copy_content_length_body(
    reader: &mut PrefixReader,
    writer: &mut TcpStream,
    length: u64,
) -> Result<(), String> {
    copy_exact_bytes(reader, writer, length)?;
    if reader.prefix_remaining() != 0 {
        return Err("request body contains pipelined bytes after Content-Length".to_string());
    }
    Ok(())
}

fn read_crlf_line_until(
    reader: &mut PrefixReader,
    limit: usize,
    deadline: Instant,
) -> Result<Vec<u8>, String> {
    let mut line = Vec::new();
    let mut byte = [0_u8; 1];
    loop {
        if line.len() >= limit {
            return Err("chunked request line exceeds the desktop proxy limit".to_string());
        }
        let read = reader.read_until_deadline(&mut byte, deadline)?;
        if read == 0 {
            return Err("chunked request body ended before its terminator".to_string());
        }
        line.push(byte[0]);
        if line.ends_with(b"\r\n") {
            return Ok(line);
        }
    }
}

fn read_crlf_line(reader: &mut PrefixReader, limit: usize) -> Result<Vec<u8>, String> {
    read_crlf_line_until(reader, limit, Instant::now() + PROXY_CLIENT_HEAD_TIMEOUT)
}

fn copy_chunked_body(reader: &mut PrefixReader, writer: &mut TcpStream) -> Result<(), String> {
    loop {
        let line = read_crlf_line(reader, MAX_CHUNK_LINE_BYTES)?;
        let line_text = std::str::from_utf8(&line[..line.len() - 2])
            .map_err(|_| "chunk size line is not ASCII".to_string())?;
        if line_text
            .bytes()
            .any(|byte| byte.is_ascii_control() || !byte.is_ascii())
        {
            return Err("chunk size line contains invalid bytes".to_string());
        }
        let size_text = line_text
            .split_once(';')
            .map_or(line_text, |(size, _)| size)
            .trim();
        if size_text.is_empty() || !size_text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("chunk size is invalid".to_string());
        }
        let size = u64::from_str_radix(size_text, 16)
            .map_err(|_| "chunk size exceeds the supported range".to_string())?;
        writer
            .write_all(&line)
            .map_err(|error| format!("failed to forward chunk size: {error}"))?;
        if size > 0 {
            reader.restore_body_idle_timeout()?;
            copy_exact_bytes(reader, writer, size)?;
            let terminator = read_crlf_line(reader, 2)?;
            if terminator != b"\r\n" {
                return Err("chunk data terminator is invalid".to_string());
            }
            writer
                .write_all(&terminator)
                .map_err(|error| format!("failed to forward chunk terminator: {error}"))?;
            continue;
        }

        let mut trailer_bytes = 0_usize;
        let trailer_deadline = Instant::now() + PROXY_CLIENT_HEAD_TIMEOUT;
        loop {
            let trailer = read_crlf_line_until(reader, MAX_CHUNK_LINE_BYTES, trailer_deadline)?;
            trailer_bytes = trailer_bytes
                .checked_add(trailer.len())
                .ok_or_else(|| "chunk trailer length overflowed".to_string())?;
            if trailer_bytes > MAX_TRAILER_BYTES {
                return Err("chunk trailers exceed the desktop proxy limit".to_string());
            }
            if trailer != b"\r\n" {
                let trailer_text = std::str::from_utf8(&trailer[..trailer.len() - 2])
                    .map_err(|_| "chunk trailer is not valid UTF-8".to_string())?;
                let (name, value) = trailer_text
                    .split_once(':')
                    .ok_or_else(|| "chunk trailer is malformed".to_string())?;
                if !is_http_token(name)
                    || value
                        .bytes()
                        .any(|byte| byte == b'\r' || byte == b'\n' || byte == 0)
                {
                    return Err("chunk trailer contains invalid bytes".to_string());
                }
                if matches!(
                    name.to_ascii_lowercase().as_str(),
                    "authorization"
                        | "connection"
                        | "content-length"
                        | "cookie"
                        | "host"
                        | "proxy-authorization"
                        | "te"
                        | "trailer"
                        | "transfer-encoding"
                        | "x-mpp-desktop-nonce"
                        | "x-mpp-desktop-session"
                        | "x-requested-with"
                ) {
                    return Err("chunk trailer contains a prohibited field".to_string());
                }
            }
            writer
                .write_all(&trailer)
                .map_err(|error| format!("failed to forward chunk trailer: {error}"))?;
            if trailer == b"\r\n" {
                break;
            }
        }
        if reader.prefix_remaining() != 0 {
            return Err(
                "chunked request contains pipelined bytes after its terminator".to_string(),
            );
        }
        return Ok(());
    }
}

fn pump_request_body(
    mut reader: PrefixReader,
    mut backend_writer: TcpStream,
    framing: RequestBodyFraming,
) {
    // Pump only the declared HTTP body framing, then half-close the private
    // request stream. Extra pipelined requests never reach the backend.
    let result = match framing {
        RequestBodyFraming::None => Ok(()),
        RequestBodyFraming::ContentLength(length) => {
            copy_content_length_body(&mut reader, &mut backend_writer, length)
        }
        RequestBodyFraming::Chunked => copy_chunked_body(&mut reader, &mut backend_writer),
    };
    if result.is_err() {
        let _ = backend_writer.shutdown(Shutdown::Both);
        let _ = reader.stream.shutdown(Shutdown::Both);
    } else {
        let _ = backend_writer.shutdown(Shutdown::Write);
    }
}

fn handle_proxy_connection(
    mut client: TcpStream,
    layout: Arc<RuntimeLayout>,
) -> Result<(), ProxyFailure> {
    client
        .set_read_timeout(Some(PROXY_CLIENT_HEAD_TIMEOUT))
        .map_err(|error| {
            ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                format!("failed to set client read deadline: {error}"),
            )
        })?;
    client
        .set_write_timeout(Some(PROXY_CLIENT_HEAD_TIMEOUT))
        .map_err(|error| {
            ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                format!("failed to set client write deadline: {error}"),
            )
        })?;
    let (message, head_end) =
        read_http_head_until(&mut client, Instant::now() + PROXY_CLIENT_HEAD_TIMEOUT)
            .map_err(|error| ProxyFailure::new(ProxyFailureKind::BadRequest, error.to_string()))?;
    let action = prepare_proxy_request(message, head_end, &layout)?;
    if let ProxyRequestAction::Preflight(response) = action {
        client.write_all(&response).map_err(|error| {
            ProxyFailure::new(
                ProxyFailureKind::BadRequest,
                format!("failed to write CORS preflight response: {error}"),
            )
        })?;
        return Ok(());
    }
    let ProxyRequestAction::Forward(request) = action else {
        unreachable!("preflight action returned above")
    };
    let cors_origin = request.cors_origin.clone();

    let mut backend = connect_authenticated_backend(&layout).map_err(|error| {
        ProxyFailure::new(ProxyFailureKind::BadGateway, error)
            .with_cors_origin(cors_origin.as_deref())
    })?;
    backend.write_all(&request.head).map_err(|error| {
        ProxyFailure::new(
            ProxyFailureKind::BadGateway,
            format!("failed to forward authenticated request headers: {error}"),
        )
        .with_cors_origin(cors_origin.as_deref())
    })?;
    client
        .set_read_timeout(Some(PROXY_CLIENT_BODY_IDLE_TIMEOUT))
        .map_err(|error| {
            ProxyFailure::new(ProxyFailureKind::BadRequest, error.to_string())
                .with_cors_origin(cors_origin.as_deref())
        })?;
    client.set_write_timeout(None).map_err(|error| {
        ProxyFailure::new(ProxyFailureKind::BadRequest, error.to_string())
            .with_cors_origin(cors_origin.as_deref())
    })?;
    let client_reader = client.try_clone().map_err(|error| {
        ProxyFailure::new(
            ProxyFailureKind::ServiceUnavailable,
            format!("failed to clone the client stream: {error}"),
        )
        .with_cors_origin(cors_origin.as_deref())
    })?;
    let backend_writer = backend.try_clone().map_err(|error| {
        ProxyFailure::new(
            ProxyFailureKind::BadGateway,
            format!("failed to clone the backend stream: {error}"),
        )
        .with_cors_origin(cors_origin.as_deref())
    })?;
    let body_reader = PrefixReader::new(request.body_prefix, client_reader);
    let framing = request.body_framing;
    thread::spawn(move || pump_request_body(body_reader, backend_writer, framing));
    let _ = io::copy(&mut backend, &mut client);
    let _ = client.shutdown(Shutdown::Both);
    let _ = backend.shutdown(Shutdown::Both);
    Ok(())
}

fn write_proxy_failure(mut client: TcpStream, failure: &ProxyFailure) {
    let _ = client.set_write_timeout(Some(PROXY_CLIENT_HEAD_TIMEOUT));
    let _ = client.write_all(&proxy_failure_response(failure));
    let _ = client.shutdown(Shutdown::Both);
}

fn run_proxy_listener(listener: TcpListener, layout: Arc<RuntimeLayout>, active: Arc<AtomicUsize>) {
    for incoming in listener.incoming() {
        let client = match incoming {
            Ok(client) => client,
            Err(_) => continue,
        };
        let reserved = active
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |current| {
                (current < MAX_PROXY_CONNECTIONS).then_some(current + 1)
            })
            .is_ok();
        if !reserved {
            write_proxy_failure(
                client,
                &ProxyFailure::new(
                    ProxyFailureKind::TooManyRequests,
                    "desktop proxy connection limit reached",
                ),
            );
            continue;
        }
        let permit = ProxyConnectionPermit {
            active: active.clone(),
        };
        let layout = layout.clone();
        thread::spawn(move || {
            let response_stream = client.try_clone().ok();
            if let Err(failure) = handle_proxy_connection(client, layout) {
                let _ = &failure.internal;
                if let Some(stream) = response_stream {
                    write_proxy_failure(stream, &failure);
                }
            }
            drop(permit);
        });
    }
}

fn start_desktop_proxy(listeners: ProxyListeners, layout: RuntimeLayout) {
    let layout = Arc::new(layout);
    let active = Arc::new(AtomicUsize::new(0));
    for listener in std::iter::once(listeners.ipv4).chain(listeners.ipv6) {
        let layout = layout.clone();
        let active = active.clone();
        thread::spawn(move || run_proxy_listener(listener, layout, active));
    }
}

fn read_bounded_log_stream<R, F>(mut reader: R, mut emit: F) -> io::Result<()>
where
    R: Read,
    F: FnMut(String),
{
    let mut chunk = [0_u8; 8 * 1024];
    let mut line = Vec::with_capacity(MAX_LOG_LINE_BYTES.min(chunk.len()));
    let mut truncated = false;
    let mut previous_was_carriage_return = false;

    let mut flush = |line: &mut Vec<u8>, truncated: &mut bool| {
        let lossy = String::from_utf8_lossy(line);
        if let Some(value) = bounded_log_line(&lossy, *truncated) {
            emit(value);
        }
        line.clear();
        *truncated = false;
    };

    loop {
        let count = reader.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        for &byte in &chunk[..count] {
            match byte {
                b'\r' => {
                    flush(&mut line, &mut truncated);
                    previous_was_carriage_return = true;
                }
                b'\n' if previous_was_carriage_return => {
                    previous_was_carriage_return = false;
                }
                b'\n' => {
                    flush(&mut line, &mut truncated);
                    previous_was_carriage_return = false;
                }
                _ => {
                    previous_was_carriage_return = false;
                    if line.len() < MAX_LOG_LINE_BYTES {
                        line.push(byte);
                    } else {
                        truncated = true;
                    }
                }
            }
        }
    }

    if !line.is_empty() || truncated {
        flush(&mut line, &mut truncated);
    }
    Ok(())
}

fn spawn_output_reader<R>(app: AppHandle, source: &'static str, pipe: R)
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        if let Err(error) = read_bounded_log_stream(pipe, |line| append_log(&app, source, line)) {
            append_log(&app, "error", format!("{source} read failed: {error}"));
        }
    });
}

#[cfg(windows)]
fn configure_backend_process_creation(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::CREATE_SUSPENDED;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
}

#[cfg(not(windows))]
fn configure_backend_process_creation(_command: &mut Command) {}

#[cfg(windows)]
fn configure_probe_process_creation(command: &mut Command) {
    configure_backend_process_creation(command);
}

#[cfg(unix)]
fn configure_probe_process_creation(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(all(not(windows), not(unix)))]
fn configure_probe_process_creation(_command: &mut Command) {}

#[cfg(windows)]
fn resume_suspended_process(child: &Child) -> Result<(), String> {
    use std::mem::size_of;
    use windows_sys::Win32::{
        Foundation::{CloseHandle, INVALID_HANDLE_VALUE},
        System::{
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD,
                THREADENTRY32,
            },
            Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
        },
    };

    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(format!(
                "CreateToolhelp32Snapshot failed while locating the suspended backend thread: {}",
                std::io::Error::last_os_error()
            ));
        }

        let mut entry = THREADENTRY32 {
            dwSize: size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        let mut thread_id = None;
        let mut has_entry = Thread32First(snapshot, &mut entry) != 0;
        while has_entry {
            if entry.th32OwnerProcessID == child.id() {
                thread_id = Some(entry.th32ThreadID);
                break;
            }
            has_entry = Thread32Next(snapshot, &mut entry) != 0;
        }
        CloseHandle(snapshot);

        let thread_id = thread_id.ok_or_else(|| {
            format!(
                "the suspended backend process {} has no discoverable primary thread",
                child.id()
            )
        })?;
        let thread = OpenThread(THREAD_SUSPEND_RESUME, 0, thread_id);
        if thread.is_null() {
            return Err(format!(
                "OpenThread failed for suspended backend thread {thread_id}: {}",
                std::io::Error::last_os_error()
            ));
        }
        let previous_suspend_count = ResumeThread(thread);
        let resume_error = if previous_suspend_count == u32::MAX {
            Some(format!(
                "ResumeThread failed for backend thread {thread_id}: {}",
                std::io::Error::last_os_error()
            ))
        } else if previous_suspend_count != 1 {
            Some(format!(
                "backend thread {thread_id} had unexpected suspend count {previous_suspend_count}"
            ))
        } else {
            None
        };
        CloseHandle(thread);
        resume_error.map_or(Ok(()), Err)
    }
}

#[cfg(not(windows))]
fn resume_suspended_process(_child: &Child) -> Result<(), String> {
    Ok(())
}

#[cfg(windows)]
fn attach_kill_on_close_job(child: &Child) -> Result<ProcessJob, String> {
    use std::{ffi::c_void, mem::size_of, os::windows::io::AsRawHandle, ptr};
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
    };

    unsafe {
        let job = CreateJobObjectW(ptr::null(), ptr::null());
        if job.is_null() {
            return Err(format!(
                "CreateJobObjectW failed: {}",
                std::io::Error::last_os_error()
            ));
        }

        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const c_void,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if configured == 0 {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("SetInformationJobObject failed: {error}"));
        }

        let process_handle = child.as_raw_handle() as *mut c_void;
        if AssignProcessToJobObject(job, process_handle) == 0 {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("AssignProcessToJobObject failed: {error}"));
        }

        Ok(ProcessJob {
            handle: Some(job as usize),
        })
    }
}

#[cfg(not(windows))]
fn attach_kill_on_close_job(_child: &Child) -> Result<ProcessJob, String> {
    Ok(ProcessJob { handle: None })
}

#[cfg(windows)]
fn close_job_handle(handle: usize) {
    use windows_sys::Win32::Foundation::CloseHandle;
    unsafe {
        CloseHandle(handle as *mut std::ffi::c_void);
    }
}

#[cfg(not(windows))]
fn close_job_handle(_handle: usize) {}

#[cfg(unix)]
fn terminate_probe_process_group(process_id: u32) -> bool {
    const SIGKILL: i32 = 9;
    unsafe extern "C" {
        fn kill(process_id: i32, signal: i32) -> i32;
    }
    let Ok(process_id) = i32::try_from(process_id) else {
        return false;
    };
    unsafe { kill(-process_id, SIGKILL) == 0 }
}

#[cfg(not(unix))]
fn terminate_probe_process_group(_process_id: u32) -> bool {
    false
}

fn terminate_probe_process_tree(job: &mut ProcessJob, child: &mut Child) {
    if job.terminate_tree() || terminate_probe_process_group(child.id()) {
        return;
    }
    let _ = child.kill();
}

fn read_bounded_probe_stream<R: Read>(mut reader: R) -> io::Result<(Vec<u8>, bool)> {
    let mut retained = Vec::with_capacity(MAX_PREFLIGHT_OUTPUT_BYTES);
    let mut chunk = [0_u8; 4096];
    let mut truncated = false;
    loop {
        let count = reader.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        let available = MAX_PREFLIGHT_OUTPUT_BYTES.saturating_sub(retained.len());
        let copied = available.min(count);
        retained.extend_from_slice(&chunk[..copied]);
        truncated |= copied < count;
    }
    Ok((retained, truncated))
}

fn collect_probe_reader(
    reader: thread::JoinHandle<io::Result<(Vec<u8>, bool)>>,
    source: &str,
) -> Result<(Vec<u8>, bool), ProbeCommandFailure> {
    reader
        .join()
        .map_err(|_| ProbeCommandFailure {
            kind: ProbeCommandFailureKind::Supervision,
            detail: format!("{source} reader panicked"),
        })?
        .map_err(|error| ProbeCommandFailure {
            kind: ProbeCommandFailureKind::Supervision,
            detail: format!("failed to read probe {source}: {error}"),
        })
}

fn run_bounded_probe_command(
    program: &Path,
    arguments: &[&str],
    timeout: Duration,
) -> Result<ProbeCommandOutput, ProbeCommandFailure> {
    let mut command = Command::new(program);
    command
        .env_clear()
        .envs(safe_inherited_environment(env::vars_os()))
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_probe_process_creation(&mut command);

    let mut child = command.spawn().map_err(|error| ProbeCommandFailure {
        kind: if error.kind() == io::ErrorKind::PermissionDenied {
            ProbeCommandFailureKind::PermissionDenied
        } else {
            ProbeCommandFailureKind::Spawn
        },
        detail: format!(
            "failed to start {}: {error}",
            bounded_preflight_path(program)
        ),
    })?;
    let mut job = match attach_kill_on_close_job(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = wait_for_child_exit_until(&mut child, Instant::now() + PROCESS_FORCE_TIMEOUT);
            return Err(ProbeCommandFailure {
                kind: ProbeCommandFailureKind::Supervision,
                detail: bounded_preflight_text(error, MAX_PREFLIGHT_DETAIL_BYTES),
            });
        }
    };
    if let Err(error) = resume_suspended_process(&child) {
        terminate_probe_process_tree(&mut job, &mut child);
        let _ = wait_for_child_exit_until(&mut child, Instant::now() + PROCESS_FORCE_TIMEOUT);
        return Err(ProbeCommandFailure {
            kind: ProbeCommandFailureKind::Supervision,
            detail: bounded_preflight_text(error, MAX_PREFLIGHT_DETAIL_BYTES),
        });
    }

    let stdout = child
        .stdout
        .take()
        .expect("probe stdout was configured as piped");
    let stderr = child
        .stderr
        .take()
        .expect("probe stderr was configured as piped");
    let stdout_reader = thread::spawn(move || read_bounded_probe_stream(stdout));
    let stderr_reader = thread::spawn(move || read_bounded_probe_stream(stderr));
    let deadline = Instant::now() + timeout;
    let mut exit_status = None;
    let mut wait_failure = None;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                exit_status = Some(status);
                break;
            }
            Ok(None) if Instant::now() >= deadline => break,
            Ok(None) => thread::sleep(
                deadline
                    .saturating_duration_since(Instant::now())
                    .min(Duration::from_millis(20)),
            ),
            Err(error) => {
                wait_failure = Some(error);
                break;
            }
        }
    }

    let timed_out = exit_status.is_none() && wait_failure.is_none();
    terminate_probe_process_tree(&mut job, &mut child);
    if exit_status.is_none() {
        let _ = wait_for_child_exit_until(&mut child, Instant::now() + PROCESS_FORCE_TIMEOUT);
    }
    let (stdout, stdout_truncated) = collect_probe_reader(stdout_reader, "stdout")?;
    let (stderr, stderr_truncated) = collect_probe_reader(stderr_reader, "stderr")?;

    if let Some(error) = wait_failure {
        return Err(ProbeCommandFailure {
            kind: ProbeCommandFailureKind::Supervision,
            detail: format!("failed to inspect probe process: {error}"),
        });
    }
    if timed_out {
        return Err(ProbeCommandFailure {
            kind: ProbeCommandFailureKind::Timeout,
            detail: format!(
                "{} exceeded its {} ms probe timeout and was terminated",
                bounded_preflight_path(program),
                timeout.as_millis()
            ),
        });
    }

    let mut combined = stdout;
    if !combined.is_empty() && !stderr.is_empty() {
        combined.push(b'\n');
    }
    let remaining = MAX_PREFLIGHT_OUTPUT_BYTES.saturating_sub(combined.len());
    combined.extend_from_slice(&stderr[..remaining.min(stderr.len())]);
    let output_was_truncated = stdout_truncated || stderr_truncated || stderr.len() > remaining;
    let mut output = String::from_utf8_lossy(&combined).trim().to_string();
    if output_was_truncated {
        output = format!(
            "{}...",
            bounded_preflight_text(output, MAX_PREFLIGHT_OUTPUT_BYTES.saturating_sub(3))
        );
    }
    Ok(ProbeCommandOutput {
        success: exit_status
            .expect("completed probe must have an exit status")
            .success(),
        output: bounded_preflight_text(output, MAX_PREFLIGHT_OUTPUT_BYTES),
    })
}

fn wait_for_child_exit_until(
    child: &mut Child,
    deadline: Instant,
) -> Result<Option<std::process::ExitStatus>, String> {
    loop {
        match child
            .try_wait()
            .map_err(|error| format!("failed to inspect backend process: {error}"))?
        {
            Some(status) => return Ok(Some(status)),
            None if Instant::now() >= deadline => return Ok(None),
            None => thread::sleep(
                deadline
                    .saturating_duration_since(Instant::now())
                    .min(Duration::from_millis(25)),
            ),
        }
    }
}

fn force_managed_backend_until(
    managed: &mut ManagedBackend,
    deadline: Instant,
) -> Result<bool, String> {
    if managed
        .child
        .try_wait()
        .map_err(|error| format!("failed to inspect backend process before termination: {error}"))?
        .is_some()
    {
        return Ok(true);
    }
    if !managed.job.terminate_tree() {
        managed
            .child
            .kill()
            .map_err(|error| format!("failed to terminate backend process: {error}"))?;
    }
    wait_for_child_exit_until(&mut managed.child, deadline).map(|status| status.is_some())
}

fn revalidate_runtime_before_spawn(layout: &RuntimeLayout) -> Result<(), String> {
    if layout.mode == RuntimeMode::Installed {
        validate_installed_runtime_manifest(&layout.runtime_root).map_err(|error| {
            format!("installed runtime revalidation failed before spawn: {error}")
        })?;
    }
    Ok(())
}

fn spawn_backend(app: &AppHandle, layout: &RuntimeLayout) -> Result<ManagedBackend, String> {
    revalidate_runtime_before_spawn(layout)?;
    let spec = build_backend_launch_spec(layout);
    append_log(
        app,
        "system",
        format!("Starting backend: {BACKEND_COMMAND}"),
    );
    append_log(
        app,
        "system",
        format!("Working directory: {}", spec.cwd.to_string_lossy()),
    );

    let mut command = Command::new(&spec.program);
    if spec.clear_environment {
        command.env_clear();
    }
    command
        .current_dir(&spec.cwd)
        .args(&spec.args)
        .envs(&spec.env)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_backend_process_creation(&mut command);

    let mut child = command
        .spawn()
        .map_err(|err| format!("failed to start backend with uv: {err}"))?;

    let mut job = match attach_kill_on_close_job(&child) {
        Ok(job) => {
            append_log(
                app,
                "system",
                "Attached backend to a kill-on-close Windows process job.",
            );
            job
        }
        Err(error) => {
            let cleanup_deadline = Instant::now() + PROCESS_FORCE_TIMEOUT;
            let _ = child.kill();
            let _ = wait_for_child_exit_until(&mut child, cleanup_deadline);
            return Err(format!(
                "backend process job setup failed and the child was reclaimed: {error}"
            ));
        }
    };
    if let Err(error) = resume_suspended_process(&child) {
        let cleanup_deadline = Instant::now() + PROCESS_FORCE_TIMEOUT;
        if !job.terminate_tree() {
            let _ = child.kill();
        }
        let _ = wait_for_child_exit_until(&mut child, cleanup_deadline);
        return Err(format!(
            "backend process could not resume after entering its Windows process job: {error}"
        ));
    }

    if let Some(stdout) = child.stdout.take() {
        spawn_output_reader(app.clone(), "stdout", stdout);
    }
    if let Some(stderr) = child.stderr.take() {
        spawn_output_reader(app.clone(), "stderr", stderr);
    }

    Ok(ManagedBackend {
        child,
        job,
        generation: 0,
    })
}

fn next_lifecycle_generation(current: u64) -> u64 {
    let next = current.wrapping_add(1);
    if next == 0 {
        1
    } else {
        next
    }
}

fn advance_lifecycle_generation(lifecycle: &mut BackendLifecycle) -> u64 {
    lifecycle.generation = next_lifecycle_generation(lifecycle.generation);
    lifecycle.generation
}

fn generation_is_current(
    lifecycle_generation: u64,
    managed_generation: Option<u64>,
    expected: u64,
) -> bool {
    lifecycle_generation == expected && managed_generation == Some(expected)
}

fn managed_generation_is_current(lifecycle: &BackendLifecycle, generation: u64) -> bool {
    generation_is_current(
        lifecycle.generation,
        lifecycle.managed.as_ref().map(|managed| managed.generation),
        generation,
    )
}

fn refresh_child_exit_locked(
    app: &AppHandle,
    lifecycle: &mut BackendLifecycle,
) -> Result<Option<String>, String> {
    let exit_status = match lifecycle.managed.as_mut() {
        Some(managed) => managed
            .child
            .try_wait()
            .map_err(|error| format!("failed to inspect backend process: {error}"))?,
        None => None,
    };
    let Some(status) = exit_status else {
        return Ok(None);
    };

    drop(lifecycle.managed.take());
    let status_text = status.to_string();
    append_log(app, "system", format!("Backend exited with {status_text}"));
    if status.success() {
        let _ = set_status(app, "stopped", None, "Backend stopped.", None)?;
    } else {
        let _ = set_status(
            app,
            "error",
            None,
            format!("Backend exited unexpectedly: {status_text}"),
            None,
        )?;
    }
    Ok(Some(status_text))
}

fn stop_managed_backend_until(
    app: &AppHandle,
    managed: &mut ManagedBackend,
    deadline: Instant,
    try_graceful: bool,
) -> Result<bool, String> {
    if managed
        .child
        .try_wait()
        .map_err(|error| format!("failed to inspect backend before shutdown: {error}"))?
        .is_some()
    {
        return Ok(true);
    }

    let graceful_deadline = deadline
        .checked_sub(PROCESS_FORCE_TIMEOUT)
        .unwrap_or(deadline);
    let graceful_request = if try_graceful && Instant::now() < deadline {
        current_runtime_layout(app).and_then(|layout| request_private_backend_shutdown(&layout))
    } else {
        Err("graceful shutdown window is unavailable".to_string())
    };
    if let Err(error) = &graceful_request {
        append_log(
            app,
            "warning",
            format!("Graceful backend shutdown request failed: {error}"),
        );
    }
    if graceful_request.is_ok() {
        match wait_for_child_exit_until(&mut managed.child, graceful_deadline) {
            Ok(Some(_)) => return Ok(true),
            Ok(None) => {}
            Err(error) => append_log(
                app,
                "warning",
                format!("Backend graceful shutdown wait failed: {error}"),
            ),
        }
    }

    append_log(
        app,
        "warning",
        "Backend remained active after graceful shutdown; forcing its managed process tree.",
    );
    force_managed_backend_until(managed, deadline)
}

fn fail_managed_backend_locked(
    app: &AppHandle,
    lifecycle: &mut BackendLifecycle,
    generation: u64,
    internal_reason: impl AsRef<str>,
    public_message: &str,
) -> Result<BackendStatus, String> {
    if !managed_generation_is_current(lifecycle, generation) {
        return current_status(app);
    }

    append_log(app, "error", internal_reason.as_ref());
    let replacement_generation = advance_lifecycle_generation(lifecycle);
    let mut managed = lifecycle
        .managed
        .take()
        .expect("current managed generation requires a process");
    managed.generation = replacement_generation;
    let pid = managed.child.id();
    let deadline = Instant::now() + BACKEND_SHUTDOWN_TIMEOUT;
    if let Err(error) = stop_managed_backend_until(app, &mut managed, deadline, true) {
        append_log(
            app,
            "error",
            format!("Backend cleanup failed after health failure: {error}"),
        );
        lifecycle.managed = Some(managed);
        return set_status(
            app,
            "error",
            Some(pid),
            format!("{public_message} Cleanup failed: {error}"),
            None,
        );
    }
    drop(managed);
    set_status(app, "error", None, public_message, None)
}

fn fail_managed_backend(
    app: &AppHandle,
    generation: u64,
    internal_reason: impl AsRef<str>,
    public_message: &str,
) -> Result<BackendStatus, String> {
    let backend = app.state::<BackendProcess>();
    let mut lifecycle = backend
        .lifecycle
        .lock()
        .map_err(|_| "backend lifecycle lock poisoned".to_string())?;
    fail_managed_backend_locked(
        app,
        &mut lifecycle,
        generation,
        internal_reason,
        public_message,
    )
}

fn cancel_managed_backend_generation(
    app: &AppHandle,
    generation: u64,
    reason: &str,
) -> Result<bool, String> {
    let backend = app.state::<BackendProcess>();
    let mut lifecycle = backend
        .lifecycle
        .lock()
        .map_err(|_| "backend lifecycle lock poisoned".to_string())?;
    if !managed_generation_is_current(&lifecycle, generation) {
        return Ok(false);
    }

    append_log(app, "system", reason);
    let replacement_generation = advance_lifecycle_generation(&mut lifecycle);
    let mut managed = lifecycle
        .managed
        .take()
        .expect("current managed generation requires a process");
    managed.generation = replacement_generation;
    let deadline = Instant::now() + BACKEND_SHUTDOWN_TIMEOUT;
    if let Err(error) = stop_managed_backend_until(app, &mut managed, deadline, true) {
        lifecycle.managed = Some(managed);
        return Err(error);
    }
    drop(managed);
    Ok(true)
}

fn start_backend_locked(
    app: &AppHandle,
    lifecycle: &mut BackendLifecycle,
) -> Result<(BackendStatus, u64), String> {
    refresh_child_exit_locked(app, lifecycle)?;

    if let Some(managed) = lifecycle.managed.as_ref() {
        let pid = managed.child.id();
        let generation = managed.generation;
        let probe = {
            let layout = current_runtime_layout(app)?;
            probe_backend(&layout.session_token, layout.backend_port)
        };
        let status = match probe {
            BackendProbe::Compatible => set_status(
                app,
                "running",
                Some(pid),
                "Managed backend is healthy.",
                None,
            )?,
            BackendProbe::Unavailable => set_status(
                app,
                "starting",
                Some(pid),
                "Managed backend is still starting.",
                None,
            )?,
            BackendProbe::Incompatible(reason) => fail_managed_backend_locked(
                app,
                lifecycle,
                generation,
                format!("managed backend health contract is incompatible: {reason}"),
                "Managed backend failed its health contract.",
            )?,
            BackendProbe::Occupied(reason) => fail_managed_backend_locked(
                app,
                lifecycle,
                generation,
                format!("an untrusted service answered for the managed backend: {reason}"),
                "Managed backend port was replaced by an untrusted service.",
            )?,
        };
        return Ok((status, lifecycle.generation));
    }

    let layout = current_runtime_layout(app)?;
    let cwd = layout.backend_dir.to_string_lossy().into_owned();
    let backend_port = layout.backend_port;
    match probe_backend(&layout.session_token, layout.backend_port) {
        BackendProbe::Unavailable => {}
        BackendProbe::Compatible => {
            return Err(format!(
                "private backend port {backend_port} already hosts an MPP backend that is not owned by this \
                 desktop session; external backends are not reused"
            ));
        }
        BackendProbe::Incompatible(reason) => {
            return Err(format!(
                "private backend port {backend_port} hosts an incompatible MPP backend: {reason}"
            ));
        }
        BackendProbe::Occupied(reason) => {
            return Err(format!(
                "private backend port {backend_port} is occupied by an untrusted service: {reason}"
            ));
        }
    }

    let generation = advance_lifecycle_generation(lifecycle);
    let _ = set_status(app, "starting", None, "Starting backend...", Some(cwd))?;
    let mut managed = match spawn_backend(app, &layout) {
        Ok(managed) => managed,
        Err(error) => {
            let _ = set_status(
                app,
                "error",
                None,
                format!("Backend process creation failed: {error}"),
                None,
            )?;
            return Err(error);
        }
    };
    let pid = managed.child.id();
    managed.generation = generation;
    lifecycle.managed = Some(managed);
    let status = set_status(
        app,
        "starting",
        Some(pid),
        "Backend process created; waiting for health check.",
        None,
    )?;
    Ok((status, generation))
}

fn start_backend(app: &AppHandle) -> Result<(BackendStatus, u64), String> {
    let backend = app.state::<BackendProcess>();
    let mut lifecycle = backend
        .lifecycle
        .lock()
        .map_err(|_| "backend lifecycle lock poisoned".to_string())?;
    start_backend_locked(app, &mut lifecycle)
}

#[derive(Debug)]
enum BackendWaitFailure {
    Internal(String),
    ProcessExited(&'static str),
    Incompatible(String),
    Occupied(String),
    Timeout,
}

impl std::fmt::Display for BackendWaitFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Internal(message) => formatter.write_str(message),
            Self::ProcessExited(message) => formatter.write_str(message),
            Self::Incompatible(reason) => {
                write!(
                    formatter,
                    "backend health contract is incompatible: {reason}"
                )
            }
            Self::Occupied(reason) => {
                write!(
                    formatter,
                    "private backend port is occupied by an untrusted service: {reason}"
                )
            }
            Self::Timeout => {
                formatter.write_str("backend did not become healthy within the wait window")
            }
        }
    }
}

fn wait_for_backend_ready(
    app: &AppHandle,
    generation: u64,
    timeout: Duration,
) -> Result<bool, BackendWaitFailure> {
    let deadline = Instant::now() + timeout;
    let backend_port = current_runtime_layout(app)
        .map_err(BackendWaitFailure::Internal)?
        .backend_port;

    loop {
        {
            let backend = app.state::<BackendProcess>();
            let mut lifecycle = backend.lifecycle.lock().map_err(|_| {
                BackendWaitFailure::Internal("backend lifecycle lock poisoned".to_string())
            })?;
            if !managed_generation_is_current(&lifecycle, generation) {
                return Ok(false);
            }
            if refresh_child_exit_locked(app, &mut lifecycle)
                .map_err(BackendWaitFailure::Internal)?
                .is_some()
            {
                return Err(BackendWaitFailure::ProcessExited(
                    "backend exited before it became healthy",
                ));
            }
        }

        let probe = {
            let layout = current_runtime_layout(app).map_err(BackendWaitFailure::Internal)?;
            probe_backend(&layout.session_token, layout.backend_port)
        };
        match probe {
            BackendProbe::Compatible => {
                let backend = app.state::<BackendProcess>();
                let mut lifecycle = backend.lifecycle.lock().map_err(|_| {
                    BackendWaitFailure::Internal("backend lifecycle lock poisoned".to_string())
                })?;
                if !managed_generation_is_current(&lifecycle, generation) {
                    return Ok(false);
                }
                if refresh_child_exit_locked(app, &mut lifecycle)
                    .map_err(BackendWaitFailure::Internal)?
                    .is_some()
                {
                    return Err(BackendWaitFailure::ProcessExited(
                        "backend exited during its health check",
                    ));
                }
                let pid = lifecycle.managed.as_ref().map(|managed| managed.child.id());
                append_log(app, "system", "Backend health contract passed.");
                let _ = set_status(app, "running", pid, "Backend is ready.", None)
                    .map_err(BackendWaitFailure::Internal)?;
                return Ok(true);
            }
            BackendProbe::Unavailable => {}
            BackendProbe::Incompatible(reason) => {
                return Err(BackendWaitFailure::Incompatible(reason));
            }
            BackendProbe::Occupied(reason) => {
                return Err(BackendWaitFailure::Occupied(format!(
                    "port {backend_port}: {reason}"
                )));
            }
        }

        if Instant::now() >= deadline {
            append_log(app, "system", "Backend health check timed out.");
            return Err(BackendWaitFailure::Timeout);
        }
        thread::sleep(
            deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(500)),
        );
    }
}

fn stop_backend_locked(
    app: &AppHandle,
    lifecycle: &mut BackendLifecycle,
) -> Result<BackendStatus, String> {
    let generation = advance_lifecycle_generation(lifecycle);
    let Some(mut managed) = lifecycle.managed.take() else {
        append_log(
            app,
            "system",
            "Stop requested while backend was not managed.",
        );
        return set_status(app, "stopped", None, "Backend stopped.", None);
    };
    managed.generation = generation;
    let pid = managed.child.id();
    append_log(app, "system", format!("Stopping backend process {pid}."));
    let _ = set_status(app, "stopping", Some(pid), "Stopping backend...", None)?;
    let deadline = Instant::now() + BACKEND_SHUTDOWN_TIMEOUT;
    match stop_managed_backend_until(app, &mut managed, deadline, true) {
        Ok(exited) => {
            if !exited {
                append_log(
                    app,
                    "warning",
                    "Backend process termination was requested at the shutdown deadline.",
                );
            }
            drop(managed);
            set_status(app, "stopped", None, "Backend stopped.", None)
        }
        Err(error) => {
            lifecycle.managed = Some(managed);
            let _ = set_status(
                app,
                "error",
                Some(pid),
                format!("Backend shutdown failed: {error}"),
                None,
            )?;
            Err(error)
        }
    }
}

fn stop_backend(app: &AppHandle) -> Result<BackendStatus, String> {
    let backend = app.state::<BackendProcess>();
    let mut lifecycle = backend
        .lifecycle
        .lock()
        .map_err(|_| "backend lifecycle lock poisoned".to_string())?;
    stop_backend_locked(app, &mut lifecycle)
}

fn restart_backend(app: &AppHandle) -> Result<(BackendStatus, u64), String> {
    let backend = app.state::<BackendProcess>();
    let mut lifecycle = backend
        .lifecycle
        .lock()
        .map_err(|_| "backend lifecycle lock poisoned".to_string())?;
    let _ = stop_backend_locked(app, &mut lifecycle)?;
    start_backend_locked(app, &mut lifecycle)
}

fn force_backend_shutdown(state: &BackendProcess) {
    let mut lifecycle = state
        .lifecycle
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let generation = advance_lifecycle_generation(&mut lifecycle);
    if let Some(mut managed) = lifecycle.managed.take() {
        managed.generation = generation;
        let _ = force_managed_backend_until(&mut managed, Instant::now() + PROCESS_FORCE_TIMEOUT);
    }
}

fn stop_backend_on_close(app: &AppHandle) {
    cancel_bootstrap_for_shutdown(app);
    if stop_backend(app).is_err() {
        let state = app.state::<BackendProcess>();
        force_backend_shutdown(&state);
    }
}

#[tauri::command]
fn backend_get_status(app: AppHandle) -> Result<BackendStatus, String> {
    if bootstrap_controls_status_for_app(&app)? {
        return current_status(&app);
    }
    let backend = app.state::<BackendProcess>();
    let mut lifecycle = backend
        .lifecycle
        .lock()
        .map_err(|_| "backend lifecycle lock poisoned".to_string())?;
    refresh_child_exit_locked(&app, &mut lifecycle)?;
    let status = current_status(&app)?;

    if let Some(managed) = lifecycle.managed.as_ref() {
        let pid = managed.child.id();
        let generation = managed.generation;
        let probe = {
            let layout = current_runtime_layout(&app)?;
            probe_backend(&layout.session_token, layout.backend_port)
        };
        return match probe {
            BackendProbe::Compatible => {
                set_status(&app, "running", Some(pid), "Backend is healthy.", None)
            }
            BackendProbe::Unavailable if status.state == "starting" => Ok(status),
            BackendProbe::Unavailable => {
                append_log(
                    &app,
                    "error",
                    "Managed backend health endpoint is unavailable.",
                );
                set_status(
                    &app,
                    "error",
                    Some(pid),
                    "Backend health check is unavailable.",
                    None,
                )
            }
            BackendProbe::Incompatible(reason) => fail_managed_backend_locked(
                &app,
                &mut lifecycle,
                generation,
                format!("managed backend health contract is incompatible: {reason}"),
                "Managed backend failed its health contract.",
            ),
            BackendProbe::Occupied(reason) => fail_managed_backend_locked(
                &app,
                &mut lifecycle,
                generation,
                format!("managed backend port answered with an untrusted service: {reason}"),
                "Managed backend port is occupied by an untrusted service.",
            ),
        };
    }

    if status.state == "stopped" {
        let (probe, backend_port) = {
            let layout = current_runtime_layout(&app)?;
            (
                probe_backend(&layout.session_token, layout.backend_port),
                layout.backend_port,
            )
        };
        match probe {
            BackendProbe::Unavailable => {}
            BackendProbe::Compatible => {
                append_log(
                    &app,
                    "error",
                    format!(
                        "Private backend port {backend_port} hosts an external MPP backend; this desktop session \
                         will not reuse it."
                    ),
                );
                return set_status(&app, "error", None, "Backend port is already in use.", None);
            }
            BackendProbe::Incompatible(reason) => {
                append_log(
                    &app,
                    "error",
                    format!(
                        "Private backend port {backend_port} hosts an incompatible MPP backend: {reason}"
                    ),
                );
                return set_status(
                    &app,
                    "error",
                    None,
                    "Backend port hosts an incompatible service.",
                    None,
                );
            }
            BackendProbe::Occupied(reason) => {
                append_log(
                    &app,
                    "error",
                    format!(
                        "Private backend port {backend_port} is occupied by an untrusted service: {reason}"
                    ),
                );
                return set_status(
                    &app,
                    "error",
                    None,
                    "Backend port is occupied by an untrusted service.",
                    None,
                );
            }
        }
    }

    current_status(&app)
}

#[tauri::command]
fn bootstrap_get_preflight(app: AppHandle) -> Result<BootstrapPreflightReport, String> {
    let controller = app.state::<BootstrapController>();
    controller
        .runtime
        .lock()
        .map(|runtime| runtime.preflight.clone())
        .map_err(|_| "bootstrap runtime lock poisoned".to_string())
}

#[tauri::command]
fn backend_get_logs(app: AppHandle) -> Result<Vec<BackendLogEntry>, String> {
    let backend = app.state::<BackendProcess>();
    backend
        .logs
        .lock()
        .map(|logs| logs.clone())
        .map_err(|_| "backend logs lock poisoned".to_string())
}

fn ensure_backend_command_available(app: &AppHandle) -> Result<(), String> {
    let controller = app.state::<BootstrapController>();
    let runtime = controller
        .runtime
        .lock()
        .map_err(|_| "bootstrap runtime lock poisoned".to_string())?;
    if runtime.shutdown_requested {
        return Err("the desktop application is shutting down".to_string());
    }
    if !runtime.bootstrap_complete || runtime.attempt_running {
        return Err(
            "backend lifecycle commands are unavailable until desktop bootstrap completes"
                .to_string(),
        );
    }
    Ok(())
}

#[tauri::command]
fn backend_start(app: AppHandle) -> Result<BackendStatus, String> {
    ensure_backend_command_available(&app)?;
    let (status, generation) = start_backend(&app)?;
    if status.state == "starting" {
        let app_for_wait = app.clone();
        thread::spawn(move || {
            if let Err(error) =
                wait_for_backend_ready(&app_for_wait, generation, Duration::from_secs(30))
            {
                let _ = fail_managed_backend(
                    &app_for_wait,
                    generation,
                    error.to_string(),
                    "Backend failed to become ready.",
                );
            }
        });
    }
    Ok(status)
}

#[tauri::command]
fn backend_stop(app: AppHandle) -> Result<BackendStatus, String> {
    ensure_backend_command_available(&app)?;
    stop_backend(&app)
}

#[tauri::command]
fn backend_restart(app: AppHandle) -> Result<BackendStatus, String> {
    ensure_backend_command_available(&app)?;
    let (status, generation) = restart_backend(&app)?;
    if status.state == "starting" {
        let app_for_wait = app.clone();
        thread::spawn(move || {
            if let Err(error) =
                wait_for_backend_ready(&app_for_wait, generation, Duration::from_secs(30))
            {
                let _ = fail_managed_backend(
                    &app_for_wait,
                    generation,
                    error.to_string(),
                    "Backend failed to become ready.",
                );
            }
        });
    }
    Ok(status)
}

fn inspect_real_file(path: &Path, label: &str) -> Result<(), String> {
    validate_existing_path_chain(path, label)?;
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
    if metadata_is_link_or_reparse(&metadata) || !metadata.is_file() {
        return Err(format!("{label} must be a real file: {}", path.display()));
    }
    Ok(())
}

fn inspect_probe_executable(path: &Path) -> Result<(), String> {
    inspect_real_file(path, "preflight executable")
}

fn executable_candidate_names(name: &OsStr) -> Vec<OsString> {
    let path = Path::new(name);
    if path.extension().is_some() {
        return vec![name.to_os_string()];
    }
    #[cfg(windows)]
    {
        let mut executable = name.to_os_string();
        executable.push(".exe");
        vec![executable, name.to_os_string()]
    }
    #[cfg(not(windows))]
    {
        vec![name.to_os_string()]
    }
}

fn resolve_probe_executable(
    configured: &OsStr,
    preferred_directories: &[PathBuf],
) -> ProbeExecutableResolution {
    let configured_path = Path::new(configured);
    if configured_path.is_absolute() || configured_path.components().count() > 1 {
        if !configured_path.is_absolute() {
            return ProbeExecutableResolution::Invalid(
                configured_path.to_path_buf(),
                "configured probe executable path must be absolute".to_string(),
            );
        }
        match fs::symlink_metadata(configured_path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return ProbeExecutableResolution::Missing;
            }
            Err(error) => {
                return ProbeExecutableResolution::Invalid(
                    configured_path.to_path_buf(),
                    format!("cannot inspect {}: {error}", configured_path.display()),
                );
            }
            Ok(_) => {}
        }
        return match inspect_probe_executable(configured_path) {
            Ok(()) => ProbeExecutableResolution::Found(configured_path.to_path_buf()),
            Err(error) => ProbeExecutableResolution::Invalid(configured_path.to_path_buf(), error),
        };
    }

    let candidate_names = executable_candidate_names(configured);
    for directory in preferred_directories {
        if !directory.is_absolute() {
            continue;
        }
        for name in &candidate_names {
            let candidate = directory.join(name);
            match fs::symlink_metadata(&candidate) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(error) => {
                    return ProbeExecutableResolution::Invalid(
                        candidate.clone(),
                        format!("cannot inspect {}: {error}", candidate.display()),
                    );
                }
                Ok(_) => {}
            }
            return match inspect_probe_executable(&candidate) {
                Ok(()) => ProbeExecutableResolution::Found(candidate),
                Err(error) => ProbeExecutableResolution::Invalid(candidate, error),
            };
        }
    }

    let Some(search_path) = env::var_os("PATH") else {
        return ProbeExecutableResolution::Missing;
    };
    for directory in env::split_paths(&search_path).take(MAX_PATH_SEARCH_ENTRIES) {
        if !directory.is_absolute() {
            continue;
        }
        for name in &candidate_names {
            let candidate = directory.join(name);
            match fs::symlink_metadata(&candidate) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(error) => {
                    return ProbeExecutableResolution::Invalid(
                        candidate.clone(),
                        format!("cannot inspect {}: {error}", candidate.display()),
                    );
                }
                Ok(_) => {}
            }
            return match inspect_probe_executable(&candidate) {
                Ok(()) => ProbeExecutableResolution::Found(candidate),
                Err(error) => ProbeExecutableResolution::Invalid(candidate, error),
            };
        }
    }
    ProbeExecutableResolution::Missing
}

fn first_probe_output_line(output: &str) -> Option<String> {
    output
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(|line| bounded_preflight_text(line, 256))
}

fn classify_python_version_output(output: &ProbeCommandOutput) -> PythonVersionOutcome {
    if !output.success {
        return PythonVersionOutcome::Invalid;
    }
    let Some(line) = first_probe_output_line(&output.output) else {
        return PythonVersionOutcome::Invalid;
    };
    let mut fields = line.split_whitespace();
    if fields.next() != Some("Python") {
        return PythonVersionOutcome::Invalid;
    }
    let Some(version) = fields.next() else {
        return PythonVersionOutcome::Invalid;
    };
    if fields.next().is_some() {
        return PythonVersionOutcome::Invalid;
    }
    let parts = version.split('.').collect::<Vec<_>>();
    if parts.len() != 3 || parts.iter().any(|part| part.parse::<u32>().is_err()) {
        return PythonVersionOutcome::Invalid;
    }
    let parsed = parts
        .iter()
        .map(|part| part.parse::<u32>().expect("numeric Python version part"))
        .collect::<Vec<_>>();
    if parsed[0] == 3 && matches!(parsed[1], 11 | 12) {
        PythonVersionOutcome::Supported(version.to_string())
    } else {
        PythonVersionOutcome::Unsupported(version.to_string())
    }
}

fn validated_media_tool_version_line(tool: &str, output: &ProbeCommandOutput) -> Option<String> {
    if !output.success {
        return None;
    }
    let line = first_probe_output_line(&output.output)?;
    let prefix = format!("{tool} version ");
    let version = line.strip_prefix(&prefix)?.split_whitespace().next()?;
    if version.is_empty() {
        return None;
    }
    Some(line)
}

fn trusted_uv_version() -> Result<String, String> {
    let contract: serde_json::Value = serde_json::from_str(TRUSTED_TOOL_CONTRACT_JSON)
        .map_err(|error| format!("compiled desktop tool contract is invalid: {error}"))?;
    json_string(&contract, "/tools/uv/version").map(str::to_string)
}

fn probe_uv_component(layout: &RuntimeLayout) -> BootstrapPreflightComponent {
    let required = layout.mode == RuntimeMode::Installed;
    let resolution = resolve_probe_executable(&layout.uv_executable, &[]);
    let executable = match resolution {
        ProbeExecutableResolution::Found(path) => path,
        ProbeExecutableResolution::Missing => {
            return preflight_component(
                "bundled-uv",
                "uv Runtime Manager",
                "missing",
                required,
                None,
                None,
                Some("UV_MISSING"),
                Some("Repair the desktop runtime or configure an available uv executable."),
                Some("The uv executable could not be resolved.".to_string()),
            );
        }
        ProbeExecutableResolution::Invalid(path, detail) => {
            return preflight_component(
                "bundled-uv",
                "uv Runtime Manager",
                "invalid",
                required,
                None,
                Some(&path),
                Some("UV_INVALID"),
                Some("Repair the uv executable and retry."),
                Some(detail),
            );
        }
    };

    let output =
        match run_bounded_probe_command(&executable, &["--version"], PREFLIGHT_PROCESS_TIMEOUT) {
            Ok(output) => output,
            Err(failure) => {
                let (status, error_code, remediation) = match failure.kind {
                    ProbeCommandFailureKind::Timeout => (
                        "blocked",
                        "UV_PROBE_TIMEOUT",
                        "Close stalled uv processes, repair the runtime if needed, and retry.",
                    ),
                    ProbeCommandFailureKind::PermissionDenied => (
                        "blocked",
                        "UV_EXECUTION_BLOCKED",
                        "Restore permission to run the uv executable and retry.",
                    ),
                    ProbeCommandFailureKind::Supervision => (
                        "blocked",
                        "UV_PROBE_BLOCKED",
                        "Restart the application and retry the supervised uv check.",
                    ),
                    ProbeCommandFailureKind::Spawn => (
                        "invalid",
                        "UV_INVALID",
                        "Repair the uv executable and retry.",
                    ),
                };
                return preflight_component(
                    "bundled-uv",
                    "uv Runtime Manager",
                    status,
                    required,
                    None,
                    Some(&executable),
                    Some(error_code),
                    Some(remediation),
                    Some(failure.detail),
                );
            }
        };
    let Some(line) = first_probe_output_line(&output.output) else {
        return preflight_component(
            "bundled-uv",
            "uv Runtime Manager",
            "invalid",
            required,
            None,
            Some(&executable),
            Some("UV_INVALID"),
            Some("Repair the uv executable and retry."),
            Some("uv --version returned no version output.".to_string()),
        );
    };
    let actual_version = line
        .strip_prefix("uv ")
        .and_then(|value| value.split_whitespace().next())
        .map(str::to_string);
    if !output.success || actual_version.is_none() {
        return preflight_component(
            "bundled-uv",
            "uv Runtime Manager",
            "invalid",
            required,
            actual_version,
            Some(&executable),
            Some("UV_INVALID"),
            Some("Repair the uv executable and retry."),
            Some(format!("uv version probe failed: {line}")),
        );
    }
    let actual_version = actual_version.expect("uv version was checked");
    let expected_version = match trusted_uv_version() {
        Ok(version) => version,
        Err(error) => {
            return preflight_component(
                "bundled-uv",
                "uv Runtime Manager",
                "invalid",
                required,
                Some(actual_version),
                Some(&executable),
                Some("UV_CONTRACT_INVALID"),
                Some("Repair or reinstall the desktop application."),
                Some(error),
            );
        }
    };
    if actual_version != expected_version {
        return preflight_component(
            "bundled-uv",
            "uv Runtime Manager",
            if required { "invalid" } else { "warning" },
            required,
            Some(actual_version.clone()),
            Some(&executable),
            Some("UV_VERSION_MISMATCH"),
            Some("Install the desktop-supported uv version or repair the bundled runtime."),
            Some(format!(
                "uv version {actual_version} does not match required version {expected_version}."
            )),
        );
    }
    preflight_component(
        "bundled-uv",
        "uv Runtime Manager",
        "ready",
        required,
        Some(actual_version),
        Some(&executable),
        None,
        None,
        Some("The uv executable and version passed the bounded process probe.".to_string()),
    )
}

fn tool_probe_failure_mapping(
    tool: &str,
    failure_kind: ProbeCommandFailureKind,
) -> (&'static str, &'static str, &'static str) {
    match (tool, failure_kind) {
        ("ffmpeg", ProbeCommandFailureKind::Timeout) => (
            "blocked",
            "FFMPEG_PROBE_TIMEOUT",
            "Close stalled FFmpeg processes and retry.",
        ),
        ("ffmpeg", ProbeCommandFailureKind::PermissionDenied) => (
            "blocked",
            "FFMPEG_EXECUTION_BLOCKED",
            "Restore permission to run FFmpeg and retry.",
        ),
        ("ffmpeg", ProbeCommandFailureKind::Supervision) => (
            "blocked",
            "FFMPEG_PROBE_BLOCKED",
            "Restart the application and retry the supervised FFmpeg check.",
        ),
        ("ffprobe", ProbeCommandFailureKind::Timeout) => (
            "blocked",
            "FFPROBE_PROBE_TIMEOUT",
            "Close stalled FFprobe processes and retry.",
        ),
        ("ffprobe", ProbeCommandFailureKind::PermissionDenied) => (
            "blocked",
            "FFPROBE_EXECUTION_BLOCKED",
            "Restore permission to run FFprobe and retry.",
        ),
        ("ffprobe", ProbeCommandFailureKind::Supervision) => (
            "blocked",
            "FFPROBE_PROBE_BLOCKED",
            "Restart the application and retry the supervised FFprobe check.",
        ),
        ("ffprobe", ProbeCommandFailureKind::Spawn) => (
            "invalid",
            "FFPROBE_INVALID",
            "Repair the FFprobe executable and retry.",
        ),
        _ => (
            "invalid",
            "FFMPEG_INVALID",
            "Repair the FFmpeg executable and retry.",
        ),
    }
}

fn python_probe_failure_mapping(
    failure_kind: ProbeCommandFailureKind,
) -> (&'static str, &'static str, &'static str) {
    match failure_kind {
        ProbeCommandFailureKind::Timeout => (
            "blocked",
            "PYTHON_PROBE_TIMEOUT",
            "Close stalled Python processes and retry.",
        ),
        ProbeCommandFailureKind::PermissionDenied => (
            "blocked",
            "PYTHON_EXECUTION_BLOCKED",
            "Restore permission to run the desktop Python environment and retry.",
        ),
        ProbeCommandFailureKind::Supervision => (
            "blocked",
            "PYTHON_PROBE_BLOCKED",
            "Restart the application and retry the supervised Python check.",
        ),
        ProbeCommandFailureKind::Spawn => (
            "invalid",
            "PYTHON_ENVIRONMENT_INVALID",
            "Repair or provision the desktop Python environment and retry.",
        ),
    }
}

fn probe_media_tool_component(
    layout: &RuntimeLayout,
    tool: &'static str,
    label: &'static str,
) -> BootstrapPreflightComponent {
    let required = layout.mode == RuntimeMode::Installed;
    let preferred = [layout.runtime_root.join("bin")];
    let resolution = resolve_probe_executable(OsStr::new(tool), &preferred);
    let (missing_code, invalid_code) = if tool == "ffprobe" {
        ("FFPROBE_MISSING", "FFPROBE_INVALID")
    } else {
        ("FFMPEG_MISSING", "FFMPEG_INVALID")
    };
    let executable = match resolution {
        ProbeExecutableResolution::Found(path) => path,
        ProbeExecutableResolution::Missing => {
            return preflight_component(
                tool,
                label,
                "missing",
                required,
                None,
                None,
                Some(missing_code),
                Some("Install FFmpeg with both ffmpeg and ffprobe available, then retry."),
                Some(format!(
                    "{label} could not be resolved from the runtime or PATH."
                )),
            );
        }
        ProbeExecutableResolution::Invalid(path, detail) => {
            return preflight_component(
                tool,
                label,
                "invalid",
                required,
                None,
                Some(&path),
                Some(invalid_code),
                Some("Repair the FFmpeg installation and retry."),
                Some(detail),
            );
        }
    };
    let output =
        match run_bounded_probe_command(&executable, &["-version"], PREFLIGHT_PROCESS_TIMEOUT) {
            Ok(output) => output,
            Err(failure) => {
                let (status, error_code, remediation) =
                    tool_probe_failure_mapping(tool, failure.kind);
                return preflight_component(
                    tool,
                    label,
                    status,
                    required,
                    None,
                    Some(&executable),
                    Some(error_code),
                    Some(remediation),
                    Some(failure.detail),
                );
            }
        };
    let version_line = validated_media_tool_version_line(tool, &output);
    if version_line.is_none() {
        return preflight_component(
            tool,
            label,
            "invalid",
            required,
            None,
            Some(&executable),
            Some(invalid_code),
            Some("Repair the FFmpeg installation and retry."),
            Some(format!(
                "{label} version probe did not return the expected fixed version signature."
            )),
        );
    }
    preflight_component(
        tool,
        label,
        "ready",
        required,
        version_line,
        Some(&executable),
        None,
        None,
        Some(format!("{label} passed the bounded process probe.")),
    )
}

fn inspect_real_directory(path: &Path, label: &str) -> Result<(), String> {
    validate_existing_path_chain(path, label)?;
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
    if metadata_is_link_or_reparse(&metadata) || !metadata.is_dir() {
        return Err(format!(
            "{label} must be a real directory: {}",
            path.display()
        ));
    }
    Ok(())
}

fn probe_directory_writable(path: &Path, index: usize) -> Result<(), String> {
    let probe_path = path.join(format!(
        ".preflight-write-{}-{}-{index}",
        std::process::id(),
        Utc::now().timestamp_micros()
    ));
    let probe_result = (|| -> io::Result<()> {
        let mut probe = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe_path)?;
        probe.write_all(b"mpp-preflight")?;
        probe.sync_all()
    })();
    if let Err(error) = probe_result {
        let _ = fs::remove_file(&probe_path);
        return Err(format!(
            "desktop data directory is not writable at {}: {error}",
            path.display()
        ));
    }
    fs::remove_file(&probe_path).map_err(|error| {
        format!(
            "desktop data write probe could not be removed from {}: {error}",
            path.display()
        )
    })
}

fn read_runtime_version(runtime_root: &Path) -> Result<String, String> {
    let path = runtime_root.join("VERSION");
    let metadata = fs::symlink_metadata(&path)
        .map_err(|error| format!("cannot inspect runtime VERSION: {error}"))?;
    if metadata_is_link_or_reparse(&metadata) || !metadata.is_file() || metadata.len() > 4096 {
        return Err("runtime VERSION must be a small regular file".to_string());
    }
    let mut bytes = Vec::with_capacity((metadata.len() as usize).saturating_add(1));
    fs::File::open(&path)
        .map_err(|error| format!("cannot open runtime VERSION: {error}"))?
        .take(4097)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("cannot read runtime VERSION: {error}"))?;
    if bytes.len() > 4096 {
        return Err("runtime VERSION changed beyond the 4096 byte limit".to_string());
    }
    let version = std::str::from_utf8(&bytes)
        .map_err(|error| format!("runtime VERSION is not UTF-8: {error}"))?;
    let version = version.trim();
    if version.is_empty() {
        return Err("runtime VERSION is empty".to_string());
    }
    Ok(bounded_preflight_text(version, 128))
}

fn probe_desktop_runtime_component(layout: &RuntimeLayout) -> BootstrapPreflightComponent {
    let required = layout.mode == RuntimeMode::Installed;
    let inspection = if required {
        validate_installed_runtime_manifest(&layout.runtime_root)
            .map_err(|error| format!("installed runtime validation failed: {error}"))
    } else {
        validate_runtime_root(&layout.runtime_root, false)
    }
    .and_then(|_| inspect_real_directory(&layout.runtime_root, "desktop runtime"))
    .and_then(|_| inspect_real_directory(&layout.backend_dir, "backend runtime"))
    .and_then(|_| inspect_real_directory(&layout.web_dist_dir, "web runtime"))
    .and_then(|_| read_runtime_version(&layout.runtime_root));
    match inspection {
        Ok(version) => preflight_component(
            "desktop-runtime",
            "Desktop Runtime",
            "ready",
            required,
            Some(version),
            Some(&layout.runtime_root),
            None,
            None,
            Some("The resolved runtime layout is complete.".to_string()),
        ),
        Err(detail) => preflight_component(
            "desktop-runtime",
            "Desktop Runtime",
            if required { "invalid" } else { "warning" },
            required,
            None,
            Some(&layout.runtime_root),
            Some("RUNTIME_INVALID"),
            Some("Repair or reinstall MediaProcessPipeline, then retry."),
            Some(detail),
        ),
    }
}

fn probe_data_root_component(layout: &RuntimeLayout) -> BootstrapPreflightComponent {
    let required = layout.mode == RuntimeMode::Installed;
    let inspection =
        inspect_real_directory(&layout.user.root, "desktop data root").and_then(|_| {
            for (index, directory) in std::iter::once(layout.user.root.as_path())
                .chain(layout.user.required_directories())
                .enumerate()
            {
                inspect_real_directory(directory, "desktop data directory")?;
                probe_directory_writable(directory, index)?;
            }
            Ok(())
        });
    match inspection {
        Ok(()) => preflight_component(
            "data-root",
            "Local Data Root",
            "ready",
            required,
            None,
            Some(&layout.user.root),
            None,
            None,
            Some("The local application directories are available.".to_string()),
        ),
        Err(detail) => preflight_component(
            "data-root",
            "Local Data Root",
            if required { "blocked" } else { "warning" },
            required,
            None,
            Some(&layout.user.root),
            Some("DATA_ROOT_UNWRITABLE"),
            Some("Restore access to the local application data directory and retry."),
            Some(detail),
        ),
    }
}

fn python_venv_executable(venv_dir: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        venv_dir.join("Scripts").join("python.exe")
    }
    #[cfg(not(windows))]
    {
        venv_dir.join("bin").join("python")
    }
}

fn probe_python_environment_component(layout: &RuntimeLayout) -> BootstrapPreflightComponent {
    let required = layout.mode == RuntimeMode::Installed;
    let python = python_venv_executable(&layout.user.venv_dir);
    if !layout.user.venv_dir.exists() || !python.exists() {
        return preflight_component(
            "python-environment",
            "Python Environment",
            if required { "missing" } else { "warning" },
            required,
            None,
            Some(&layout.user.venv_dir),
            Some("PYTHON_ENVIRONMENT_MISSING"),
            Some("Provision the desktop Python environment and retry."),
            Some(
                "The configured user virtual environment is absent; preflight does not create or synchronize it."
                    .to_string(),
            ),
        );
    }
    let inspection = inspect_real_directory(&layout.user.venv_dir, "Python virtual environment")
        .and_then(|_| {
            inspect_real_file(
                &layout.user.venv_dir.join("pyvenv.cfg"),
                "Python virtual environment marker",
            )
        })
        .and_then(|_| inspect_probe_executable(&python));
    if let Err(detail) = inspection {
        return preflight_component(
            "python-environment",
            "Python Environment",
            if required { "invalid" } else { "warning" },
            required,
            None,
            Some(&layout.user.venv_dir),
            Some("PYTHON_ENVIRONMENT_INVALID"),
            Some("Remove the damaged virtual environment and provision it again."),
            Some(detail),
        );
    }

    let output = match run_bounded_probe_command(
        &python,
        &["-I", "-E", "-s", "--version"],
        PREFLIGHT_PROCESS_TIMEOUT,
    ) {
        Ok(output) => output,
        Err(failure) => {
            let (status, error_code, remediation) = python_probe_failure_mapping(failure.kind);
            return preflight_component(
                "python-environment",
                "Python Environment",
                status,
                required,
                None,
                Some(&layout.user.venv_dir),
                Some(error_code),
                Some(remediation),
                Some(failure.detail),
            );
        }
    };

    match classify_python_version_output(&output) {
        PythonVersionOutcome::Supported(version) => preflight_component(
            "python-environment",
            "Python Environment",
            "ready",
            required,
            Some(version),
            Some(&layout.user.venv_dir),
            None,
            None,
            Some("Python 3.11 or 3.12 passed the bounded process probe.".to_string()),
        ),
        PythonVersionOutcome::Unsupported(version) => preflight_component(
            "python-environment",
            "Python Environment",
            if required { "invalid" } else { "warning" },
            required,
            Some(version),
            Some(&layout.user.venv_dir),
            Some("PYTHON_VERSION_UNSUPPORTED"),
            Some("Provision a Python 3.11 or 3.12 desktop environment and retry."),
            Some(
                "The desktop Python version is outside the supported 3.11-3.12 range.".to_string(),
            ),
        ),
        PythonVersionOutcome::Invalid => preflight_component(
            "python-environment",
            "Python Environment",
            if required { "invalid" } else { "warning" },
            required,
            None,
            Some(&layout.user.venv_dir),
            Some("PYTHON_ENVIRONMENT_INVALID"),
            Some("Repair or provision the desktop Python environment and retry."),
            Some("Python did not return the expected fixed version signature.".to_string()),
        ),
    }
}

fn classify_settings_preflight_output(output: &ProbeCommandOutput) -> SettingsPreflightOutcome {
    match (output.success, output.output.as_str()) {
        (true, SETTINGS_PREFLIGHT_OK_TOKEN) => SettingsPreflightOutcome::Valid,
        (false, SETTINGS_PREFLIGHT_INVALID_TOKEN) => SettingsPreflightOutcome::Invalid,
        _ => SettingsPreflightOutcome::Error,
    }
}

fn probe_runtime_settings_component(layout: &RuntimeLayout) -> BootstrapPreflightComponent {
    let required = layout.mode == RuntimeMode::Installed;
    let path = &layout.user.config_file;
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return preflight_component(
                "runtime-settings",
                "Runtime Settings",
                "ready",
                required,
                None,
                Some(path),
                None,
                None,
                Some("No settings file exists; runtime defaults will be used.".to_string()),
            );
        }
        Err(error) => {
            return preflight_component(
                "runtime-settings",
                "Runtime Settings",
                "invalid",
                required,
                None,
                Some(path),
                Some("CONFIG_INVALID"),
                Some("Correct or remove the damaged runtime settings file, then retry."),
                Some(format!("cannot inspect runtime settings: {error}")),
            );
        }
        Ok(_) => {}
    }
    let inspection = (|| -> Result<(), String> {
        validate_existing_path_chain(path, "runtime settings")?;
        let metadata = fs::symlink_metadata(path)
            .map_err(|error| format!("cannot inspect runtime settings: {error}"))?;
        if metadata_is_link_or_reparse(&metadata) || !metadata.is_file() {
            return Err("runtime settings must be a regular file".to_string());
        }
        if metadata.len() > MAX_RUNTIME_SETTINGS_BYTES {
            return Err(format!(
                "runtime settings exceed the {} byte limit",
                MAX_RUNTIME_SETTINGS_BYTES
            ));
        }
        let mut bytes = Vec::with_capacity((metadata.len() as usize).saturating_add(1));
        fs::File::open(path)
            .map_err(|error| format!("cannot open runtime settings: {error}"))?
            .take(MAX_RUNTIME_SETTINGS_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| format!("cannot read runtime settings: {error}"))?;
        if bytes.len() as u64 > MAX_RUNTIME_SETTINGS_BYTES {
            return Err(format!(
                "runtime settings changed beyond the {} byte limit",
                MAX_RUNTIME_SETTINGS_BYTES
            ));
        }
        let value: serde_json::Value = serde_json::from_slice(&bytes)
            .map_err(|error| format!("runtime settings JSON is invalid: {error}"))?;
        if !value.is_object() {
            return Err("runtime settings JSON root must be an object".to_string());
        }
        Ok(())
    })();
    if let Err(detail) = inspection {
        return preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "invalid",
            required,
            None,
            Some(path),
            Some("CONFIG_INVALID"),
            Some("Correct or remove the damaged runtime settings file, then retry."),
            Some(detail),
        );
    }

    if !required {
        return preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "ready",
            false,
            None,
            Some(path),
            None,
            None,
            Some("The runtime settings JSON structure is valid.".to_string()),
        );
    }

    let python = python_venv_executable(&layout.user.venv_dir);
    let helper = layout
        .backend_dir
        .join("app")
        .join("services")
        .join("settings_preflight.py");
    if inspect_probe_executable(&python).is_err()
        || inspect_real_file(&helper, "settings preflight helper").is_err()
    {
        return preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "blocked",
            true,
            None,
            Some(path),
            Some("CONFIG_PREFLIGHT_UNAVAILABLE"),
            Some("Repair the desktop Python environment or application runtime, then retry."),
            Some(
                "RuntimeSettings semantic validation is unavailable in the installed runtime."
                    .to_string(),
            ),
        );
    }

    let helper_argument = helper.to_string_lossy().into_owned();
    let config_argument = path.to_string_lossy().into_owned();
    let arguments = ["-I", helper_argument.as_str(), config_argument.as_str()];
    let output = match run_bounded_probe_command(&python, &arguments, PREFLIGHT_PROCESS_TIMEOUT) {
        Ok(output) => output,
        Err(failure) => {
            let (error_code, remediation, detail) = match failure.kind {
                ProbeCommandFailureKind::Timeout => (
                    "CONFIG_PREFLIGHT_TIMEOUT",
                    "Close stalled Python processes and retry.",
                    "RuntimeSettings semantic validation exceeded its time limit.",
                ),
                ProbeCommandFailureKind::PermissionDenied => (
                    "CONFIG_PREFLIGHT_EXECUTION_BLOCKED",
                    "Restore permission to run the desktop Python environment and retry.",
                    "The desktop Python environment could not execute settings validation.",
                ),
                ProbeCommandFailureKind::Spawn | ProbeCommandFailureKind::Supervision => (
                    "CONFIG_PREFLIGHT_ERROR",
                    "Repair the desktop Python environment or application runtime, then retry.",
                    "RuntimeSettings semantic validation could not run safely.",
                ),
            };
            return preflight_component(
                "runtime-settings",
                "Runtime Settings",
                "blocked",
                true,
                None,
                Some(path),
                Some(error_code),
                Some(remediation),
                Some(detail.to_string()),
            );
        }
    };

    match classify_settings_preflight_output(&output) {
        SettingsPreflightOutcome::Valid => preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "ready",
            true,
            None,
            Some(path),
            None,
            None,
            Some(
                "The settings file passed structural and RuntimeSettings semantic validation."
                    .to_string(),
            ),
        ),
        SettingsPreflightOutcome::Invalid => preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "invalid",
            true,
            None,
            Some(path),
            Some("CONFIG_INVALID"),
            Some("Correct or remove the damaged runtime settings file, then retry."),
            Some("Runtime settings failed semantic validation.".to_string()),
        ),
        SettingsPreflightOutcome::Error => preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "blocked",
            true,
            None,
            Some(path),
            Some("CONFIG_PREFLIGHT_ERROR"),
            Some("Repair the desktop Python environment or application runtime, then retry."),
            Some(
                "RuntimeSettings semantic validation returned an invalid fixed contract."
                    .to_string(),
            ),
        ),
    }
}

fn probe_port_component(
    component_id: &str,
    label: &str,
    port: u16,
    already_owned: bool,
) -> BootstrapPreflightComponent {
    if already_owned {
        return preflight_component(
            component_id,
            label,
            "ready",
            true,
            None,
            None,
            None,
            None,
            Some(format!(
                "localhost port {port} is owned by this desktop session."
            )),
        );
    }
    match bind_desktop_proxy_at(port) {
        Ok(listeners) => {
            drop(listeners);
            preflight_component(
                component_id,
                label,
                "ready",
                true,
                None,
                None,
                None,
                None,
                Some(format!("localhost port {port} is available.")),
            )
        }
        Err(detail) => {
            let (error_code, remediation) = if component_id == "desktop-proxy-port" {
                (
                    "PORT_IN_USE",
                    "Close the process using localhost port 18000 and retry.",
                )
            } else {
                (
                    "PRIVATE_PORT_IN_USE",
                    "Close the process using the private backend port and retry.",
                )
            };
            preflight_component(
                component_id,
                label,
                "blocked",
                true,
                None,
                None,
                Some(error_code),
                Some(remediation),
                Some(detail),
            )
        }
    }
}

fn build_bootstrap_preflight(
    app: &AppHandle,
    layout: &RuntimeLayout,
    proxy_started: bool,
) -> BootstrapPreflightReport {
    let webview = if app.get_webview_window("main").is_some() {
        preflight_component(
            "webview2",
            "Microsoft Edge WebView2",
            "ready",
            true,
            None,
            None,
            None,
            None,
            Some("The desktop WebView is available.".to_string()),
        )
    } else {
        preflight_component(
            "webview2",
            "Microsoft Edge WebView2",
            "blocked",
            true,
            None,
            None,
            Some("WEBVIEW2_MISSING"),
            Some(
                "Install or repair Microsoft Edge WebView2 Runtime, then restart the application.",
            ),
            Some("The main desktop WebView is unavailable.".to_string()),
        )
    };
    finalize_preflight_report(vec![
        probe_desktop_runtime_component(layout),
        probe_data_root_component(layout),
        probe_uv_component(layout),
        probe_python_environment_component(layout),
        probe_media_tool_component(layout, "ffmpeg", "FFmpeg"),
        probe_media_tool_component(layout, "ffprobe", "FFprobe"),
        probe_port_component(
            "desktop-proxy-port",
            "Desktop Proxy Port",
            BACKEND_PORT,
            proxy_started,
        ),
        probe_port_component(
            "backend-private-port",
            "Backend Private Port",
            layout.backend_port,
            false,
        ),
        probe_runtime_settings_component(layout),
        webview,
    ])
}

fn preflight_report_from_failure(failure: &BootstrapFailure) -> BootstrapPreflightReport {
    let mut report = finalize_preflight_report(
        PREFLIGHT_COMPONENT_ORDER
            .iter()
            .map(|component_id| {
                preflight_component(
                    component_id,
                    preflight_component_label(component_id),
                    "blocked",
                    true,
                    None,
                    None,
                    Some("PREFLIGHT_SCAN_BLOCKED"),
                    Some(
                        "Resolve the reported bootstrap failure, then retry the environment scan.",
                    ),
                    Some(format!(
                        "{} could not be evaluated because bootstrap initialization stopped early.",
                        preflight_component_label(component_id)
                    )),
                )
            })
            .collect(),
    );
    apply_failure_to_preflight(&mut report, failure);
    report
}

fn bootstrap_internal_failure(error: impl Into<String>) -> BootstrapFailure {
    BootstrapFailure::manual(
        "DESKTOP_BOOTSTRAP_FAILED",
        "desktop-bootstrap",
        "Open diagnostics, restart the application, and retry.",
        error,
        None,
    )
}

fn bootstrap_wait_failure(error: BackendWaitFailure, layout: &RuntimeLayout) -> BootstrapFailure {
    let local_path = Some(layout.backend_dir.as_path());
    match error {
        BackendWaitFailure::ProcessExited(detail) => BootstrapFailure::retryable(
            "BACKEND_EXITED",
            "python-runtime",
            "Open the logs, correct the reported runtime or configuration error, and retry.",
            detail,
            local_path,
        ),
        BackendWaitFailure::Incompatible(detail) => BootstrapFailure::manual(
            "BACKEND_HEALTH_INVALID",
            "backend-health",
            "Repair or reinstall the matching desktop runtime, then retry.",
            detail,
            local_path,
        ),
        BackendWaitFailure::Occupied(detail) => BootstrapFailure::retryable(
            "PRIVATE_PORT_IN_USE",
            "backend-port",
            "Close the conflicting local process and retry.",
            detail,
            None,
        ),
        BackendWaitFailure::Timeout => BootstrapFailure::retryable(
            "BACKEND_START_TIMEOUT",
            "backend-health",
            "Open the logs, check runtime initialization and network access, then retry.",
            BackendWaitFailure::Timeout.to_string(),
            local_path,
        ),
        BackendWaitFailure::Internal(detail) => bootstrap_internal_failure(detail),
    }
}

fn prepare_bootstrap_runtime(app: &AppHandle) -> Result<RuntimeLayout, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    if let Some(layout) = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?
        .layout
        .clone()
    {
        return Ok(layout);
    }

    let layout = resolve_runtime_layout(app)?;
    let mut runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    if runtime.layout.is_none() {
        runtime.layout = Some(layout.clone());
    }
    Ok(runtime.layout.clone().unwrap_or(layout))
}

fn ensure_bootstrap_proxy(
    app: &AppHandle,
    layout: &RuntimeLayout,
    attempt_epoch: u64,
) -> Result<bool, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    let mut runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    if !bootstrap_attempt_is_current(&runtime, attempt_epoch) {
        return Ok(false);
    }
    if runtime.proxy_started {
        return Ok(true);
    }

    let listeners = bind_desktop_proxy_at(BACKEND_PORT).map_err(|error| {
        BootstrapFailure::retryable(
            "PORT_IN_USE",
            "desktop-proxy",
            "Close the process using localhost port 18000 and retry.",
            error,
            None,
        )
    })?;
    start_desktop_proxy(listeners, layout.clone());
    runtime.proxy_started = true;
    Ok(true)
}

fn complete_bootstrap_navigation(
    app: &AppHandle,
    layout: &RuntimeLayout,
    attempt_epoch: u64,
) -> Result<BootstrapAttemptOutcome, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    let mut runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    if !bootstrap_attempt_is_current(&runtime, attempt_epoch) {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }
    let window = app.get_webview_window("main").ok_or_else(|| {
        BootstrapFailure::manual(
            "WEBVIEW_UNAVAILABLE",
            "desktop-webview",
            "Restart the application. Repair the WebView2 runtime if the problem continues.",
            "the main desktop WebView is unavailable",
            None,
        )
    })?;
    let proxy_cookie = Cookie::build((DESKTOP_PROXY_COOKIE, layout.proxy_token.clone()))
        .domain(DESKTOP_API_HOST)
        .path("/")
        .http_only(true)
        .same_site(SameSite::Lax)
        .secure(false)
        .build();
    window.set_cookie(proxy_cookie).map_err(|error| {
        BootstrapFailure::retryable(
            "PROXY_SESSION_FAILED",
            "desktop-proxy",
            "Restart the application and retry.",
            format!("failed to install desktop proxy cookie: {error}"),
            None,
        )
    })?;
    window
        .navigate(packaged_app_navigation_url().map_err(bootstrap_internal_failure)?)
        .map_err(|error| {
            BootstrapFailure::retryable(
                "APP_NAVIGATION_FAILED",
                "desktop-webview",
                "Restart the application and retry.",
                format!("failed to load the packaged app UI: {error}"),
                None,
            )
        })?;
    set_bootstrap_phase(
        app,
        "APP_READY",
        "The desktop application is ready.",
        Some(&layout.runtime_root),
    )
    .map_err(bootstrap_internal_failure)?;
    runtime.bootstrap_complete = true;
    Ok(BootstrapAttemptOutcome::Completed)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BootstrapAttemptOutcome {
    Completed,
    Cancelled,
}

fn next_bootstrap_epoch(current: u64) -> u64 {
    next_lifecycle_generation(current)
}

fn bootstrap_attempt_is_current(runtime: &BootstrapRuntime, attempt_epoch: u64) -> bool {
    runtime.attempt_running && runtime.attempt_epoch == attempt_epoch && !runtime.shutdown_requested
}

fn bootstrap_attempt_is_current_for_app(
    app: &AppHandle,
    attempt_epoch: u64,
) -> Result<bool, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    let runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    Ok(bootstrap_attempt_is_current(&runtime, attempt_epoch))
}

fn bootstrap_proxy_state_for_attempt(
    app: &AppHandle,
    attempt_epoch: u64,
) -> Result<Option<bool>, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    let runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    Ok(bootstrap_attempt_is_current(&runtime, attempt_epoch).then_some(runtime.proxy_started))
}

fn cache_preflight_for_attempt(
    app: &AppHandle,
    attempt_epoch: u64,
    report: BootstrapPreflightReport,
) -> Result<bool, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    let mut runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    if !bootstrap_attempt_is_current(&runtime, attempt_epoch) {
        return Ok(false);
    }
    runtime.preflight = report;
    Ok(true)
}

fn set_bootstrap_phase_for_attempt(
    app: &AppHandle,
    attempt_epoch: u64,
    phase: &str,
    message: impl Into<String>,
    local_path: Option<&Path>,
) -> Result<bool, BootstrapFailure> {
    let controller = app.state::<BootstrapController>();
    let runtime = controller
        .runtime
        .lock()
        .map_err(|_| bootstrap_internal_failure("bootstrap runtime lock poisoned"))?;
    if !bootstrap_attempt_is_current(&runtime, attempt_epoch) {
        return Ok(false);
    }
    set_bootstrap_phase(app, phase, message, local_path).map_err(bootstrap_internal_failure)?;
    Ok(true)
}

fn publish_bootstrap_failure_for_attempt(
    app: &AppHandle,
    attempt_epoch: u64,
    failure: &BootstrapFailure,
) -> Result<bool, String> {
    let controller = app.state::<BootstrapController>();
    let mut runtime = controller
        .runtime
        .lock()
        .map_err(|_| "bootstrap runtime lock poisoned".to_string())?;
    if !bootstrap_attempt_is_current(&runtime, attempt_epoch) {
        return Ok(false);
    }
    apply_failure_to_preflight(&mut runtime.preflight, failure);
    drop(runtime);
    set_bootstrap_failure(app, failure)?;
    Ok(true)
}

fn run_bootstrap_attempt(
    app: &AppHandle,
    attempt_epoch: u64,
) -> Result<BootstrapAttemptOutcome, BootstrapFailure> {
    if !bootstrap_attempt_is_current_for_app(app, attempt_epoch)? {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }
    let layout = match prepare_bootstrap_runtime(app) {
        Ok(layout) => layout,
        Err(failure) => {
            let report = preflight_report_from_failure(&failure);
            let _ = cache_preflight_for_attempt(app, attempt_epoch, report);
            return Err(failure);
        }
    };
    let Some(proxy_started) = bootstrap_proxy_state_for_attempt(app, attempt_epoch)? else {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    };
    let preflight = build_bootstrap_preflight(app, &layout, proxy_started);
    if !cache_preflight_for_attempt(app, attempt_epoch, preflight.clone())? {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }
    if let Some(failure) = preflight_blocking_failure(&preflight) {
        return Err(failure);
    }
    if !ensure_bootstrap_proxy(app, &layout, attempt_epoch)? {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }
    if !set_bootstrap_phase_for_attempt(
        app,
        attempt_epoch,
        "READY_TO_START",
        "The desktop runtime is ready to start.",
        Some(&layout.runtime_root),
    )? {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }
    if !set_bootstrap_phase_for_attempt(
        app,
        attempt_epoch,
        "STARTING_BACKEND",
        "Starting the local backend.",
        Some(&layout.backend_dir),
    )? {
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }

    let (status, generation) = start_backend(app).map_err(|error| {
        BootstrapFailure::retryable(
            "BACKEND_START_FAILED",
            "python-runtime",
            "Open the logs, verify the bundled runtime, and retry.",
            error,
            Some(&layout.backend_dir),
        )
    })?;
    if !bootstrap_attempt_is_current_for_app(app, attempt_epoch)? {
        let _ = cancel_managed_backend_generation(
            app,
            generation,
            "desktop bootstrap was cancelled after backend process creation",
        );
        return Ok(BootstrapAttemptOutcome::Cancelled);
    }
    if status.state == "starting" {
        if !set_bootstrap_phase_for_attempt(
            app,
            attempt_epoch,
            "WAITING_HEALTH",
            "Waiting for the authenticated backend health check.",
            Some(&layout.backend_dir),
        )? {
            return Ok(BootstrapAttemptOutcome::Cancelled);
        }
        match wait_for_backend_ready(app, generation, Duration::from_secs(90)) {
            Ok(true) => {}
            Ok(false) => {
                return Ok(BootstrapAttemptOutcome::Cancelled);
            }
            Err(error) => {
                if !bootstrap_attempt_is_current_for_app(app, attempt_epoch)? {
                    return Ok(BootstrapAttemptOutcome::Cancelled);
                }
                let failure = bootstrap_wait_failure(error, &layout);
                let _ = fail_managed_backend(
                    app,
                    generation,
                    &failure.detail,
                    "Backend failed to become ready.",
                );
                return Err(failure);
            }
        }
    } else if status.state != "running" {
        return Err(BootstrapFailure::retryable(
            "BACKEND_START_FAILED",
            "python-runtime",
            "Open the logs, verify the bundled runtime, and retry.",
            status.message,
            Some(&layout.backend_dir),
        ));
    }

    complete_bootstrap_navigation(app, &layout, attempt_epoch)
}

fn finish_bootstrap_attempt(app: &AppHandle, attempt_epoch: u64) {
    let controller = app.state::<BootstrapController>();
    let mut runtime = controller
        .runtime
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    end_bootstrap_attempt(&mut runtime, attempt_epoch);
}

fn begin_bootstrap_attempt(runtime: &mut BootstrapRuntime) -> Option<u64> {
    if runtime.attempt_running || runtime.bootstrap_complete || runtime.shutdown_requested {
        return None;
    }
    runtime.attempt_epoch = next_bootstrap_epoch(runtime.attempt_epoch);
    runtime.attempt_running = true;
    runtime.preflight = BootstrapPreflightReport::default();
    Some(runtime.attempt_epoch)
}

fn end_bootstrap_attempt(runtime: &mut BootstrapRuntime, attempt_epoch: u64) {
    if runtime.attempt_epoch == attempt_epoch {
        runtime.attempt_running = false;
    }
}

fn cancel_bootstrap_runtime_for_shutdown(runtime: &mut BootstrapRuntime) {
    runtime.shutdown_requested = true;
    runtime.bootstrap_complete = false;
    runtime.attempt_running = false;
    runtime.attempt_epoch = next_bootstrap_epoch(runtime.attempt_epoch);
}

fn cancel_bootstrap_for_shutdown(app: &AppHandle) {
    let controller = app.state::<BootstrapController>();
    let mut runtime = controller
        .runtime
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    cancel_bootstrap_runtime_for_shutdown(&mut runtime);
}

fn schedule_bootstrap_attempt(app: &AppHandle) -> Result<BackendStatus, String> {
    let controller = app.state::<BootstrapController>();
    let attempt_epoch = {
        let mut runtime = controller
            .runtime
            .lock()
            .map_err(|_| "bootstrap runtime lock poisoned".to_string())?;
        let Some(attempt_epoch) = begin_bootstrap_attempt(&mut runtime) else {
            return current_status(app);
        };
        attempt_epoch
    };

    match set_bootstrap_phase_for_attempt(
        app,
        attempt_epoch,
        "SCANNING",
        "Scanning the installed runtime.",
        None,
    ) {
        Ok(true) => {}
        Ok(false) => {
            finish_bootstrap_attempt(app, attempt_epoch);
            return current_status(app);
        }
        Err(error) => {
            finish_bootstrap_attempt(app, attempt_epoch);
            return Err(error.detail);
        }
    }
    let app_for_attempt = app.clone();
    thread::spawn(move || {
        if let Err(failure) = run_bootstrap_attempt(&app_for_attempt, attempt_epoch) {
            let _ =
                publish_bootstrap_failure_for_attempt(&app_for_attempt, attempt_epoch, &failure);
        }
        finish_bootstrap_attempt(&app_for_attempt, attempt_epoch);
    });
    current_status(app)
}

#[tauri::command]
fn backend_retry(app: AppHandle) -> Result<BackendStatus, String> {
    schedule_bootstrap_attempt(&app)
}

fn bootstrap_log_directory(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(layout) = current_runtime_layout(app) {
        return Ok(layout.user.log_dir);
    }
    app.state::<BootstrapController>()
        .fallback_log_dir
        .clone()
        .ok_or_else(|| "the local desktop log directory is unavailable".to_string())
}

fn open_local_path(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        use std::{ffi::OsStr, iter::once, os::windows::ffi::OsStrExt, ptr};
        use windows_sys::Win32::UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL};

        let operation = OsStr::new("open")
            .encode_wide()
            .chain(once(0))
            .collect::<Vec<_>>();
        let target = path
            .as_os_str()
            .encode_wide()
            .chain(once(0))
            .collect::<Vec<_>>();
        let result = unsafe {
            ShellExecuteW(
                ptr::null_mut(),
                operation.as_ptr(),
                target.as_ptr(),
                ptr::null(),
                ptr::null(),
                SW_SHOWNORMAL,
            )
        };
        let result_code = result as isize;
        if result_code <= 32 {
            return Err(format!(
                "Windows could not open the log directory (ShellExecuteW code {result_code})"
            ));
        }
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(path)
            .spawn()
            .map_err(|error| format!("failed to open the log directory: {error}"))?;
        return Ok(());
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(path)
            .spawn()
            .map_err(|error| format!("failed to open the log directory: {error}"))?;
        return Ok(());
    }
}

fn validate_bootstrap_diagnostics_target(path: &Path) -> Result<(), String> {
    validate_existing_path_chain(path, "desktop bootstrap diagnostics file")?;
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata_is_link_or_reparse(&metadata) => Err(format!(
            "desktop bootstrap diagnostics file must not be a symlink or reparse point: {}",
            path.display()
        )),
        Ok(metadata) if !metadata.is_file() => Err(format!(
            "desktop bootstrap diagnostics target must be a regular file: {}",
            path.display()
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!(
            "failed to inspect desktop bootstrap diagnostics target {}: {error}",
            path.display()
        )),
    }
}

fn write_bootstrap_diagnostics(log_dir: &Path, snapshot: &[u8]) -> Result<(), String> {
    validate_existing_path_chain(log_dir, "desktop log directory")?;
    fs::create_dir_all(log_dir).map_err(|error| {
        format!(
            "failed to create log directory {}: {error}",
            log_dir.display()
        )
    })?;
    validate_existing_path_chain(log_dir, "desktop log directory")?;
    let log_metadata = fs::symlink_metadata(log_dir).map_err(|error| {
        format!(
            "failed to inspect desktop log directory {}: {error}",
            log_dir.display()
        )
    })?;
    if metadata_is_link_or_reparse(&log_metadata) || !log_metadata.is_dir() {
        return Err(format!(
            "desktop log directory must be a real directory: {}",
            log_dir.display()
        ));
    }

    let destination = log_dir.join("desktop-bootstrap.json");
    validate_bootstrap_diagnostics_target(&destination)?;
    let temporary_name = format!(
        ".desktop-bootstrap-{}.tmp",
        generate_session_token()
            .map_err(|error| format!("failed to create diagnostics file identity: {error}"))?
    );
    let temporary = log_dir.join(temporary_name);
    let write_result = (|| -> Result<(), String> {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|error| {
                format!(
                    "failed to create temporary desktop diagnostics file {}: {error}",
                    temporary.display()
                )
            })?;
        file.write_all(snapshot).map_err(|error| {
            format!(
                "failed to write temporary desktop diagnostics file {}: {error}",
                temporary.display()
            )
        })?;
        file.flush().map_err(|error| {
            format!(
                "failed to flush temporary desktop diagnostics file {}: {error}",
                temporary.display()
            )
        })?;
        file.sync_all().map_err(|error| {
            format!(
                "failed to persist temporary desktop diagnostics file {}: {error}",
                temporary.display()
            )
        })?;
        drop(file);

        validate_existing_path_chain(log_dir, "desktop log directory")?;
        let log_metadata = fs::symlink_metadata(log_dir).map_err(|error| {
            format!(
                "failed to re-inspect desktop log directory {}: {error}",
                log_dir.display()
            )
        })?;
        if metadata_is_link_or_reparse(&log_metadata) || !log_metadata.is_dir() {
            return Err(format!(
                "desktop log directory changed before diagnostics replacement: {}",
                log_dir.display()
            ));
        }
        validate_bootstrap_diagnostics_target(&destination)?;
        match fs::symlink_metadata(&destination) {
            Ok(_) => fs::remove_file(&destination).map_err(|error| {
                format!(
                    "failed to remove the previous desktop diagnostics file {}: {error}",
                    destination.display()
                )
            })?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(format!(
                    "failed to inspect the previous desktop diagnostics file {}: {error}",
                    destination.display()
                ));
            }
        }
        fs::rename(&temporary, &destination).map_err(|error| {
            format!(
                "failed to replace desktop diagnostics file {}: {error}",
                destination.display()
            )
        })?;
        Ok(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    write_result
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<(), String> {
    let log_dir = bootstrap_log_directory(&app)?;
    let status = current_status(&app)?;
    let logs = backend_get_logs(app.clone())?;
    let snapshot = serde_json::to_vec_pretty(&serde_json::json!({
        "status": status,
        "logs": logs,
    }))
    .map_err(|error| format!("failed to serialize desktop diagnostics: {error}"))?;
    write_bootstrap_diagnostics(&log_dir, &snapshot)?;
    open_local_path(&log_dir)
}

fn validated_external_url(value: &str) -> Result<String, String> {
    const MAX_EXTERNAL_URL_BYTES: usize = 8 * 1024;
    let trimmed = value.trim();
    if trimmed.is_empty()
        || trimmed.len() > MAX_EXTERNAL_URL_BYTES
        || trimmed.chars().any(|character| {
            character.is_control() || character.is_whitespace() || character == '\\'
        })
    {
        return Err("invalid URL".to_string());
    }
    let parsed = url::Url::parse(trimmed).map_err(|_| "invalid URL".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("only absolute http(s) URLs can be opened".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("URLs containing credentials cannot be opened".to_string());
    }
    Ok(parsed.into())
}

#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let validated = validated_external_url(&url)?;

    #[cfg(target_os = "windows")]
    {
        use std::{ffi::OsStr, iter::once, os::windows::ffi::OsStrExt, ptr};
        use windows_sys::Win32::UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL};

        let operation = OsStr::new("open")
            .encode_wide()
            .chain(once(0))
            .collect::<Vec<_>>();
        let target = OsStr::new(&validated)
            .encode_wide()
            .chain(once(0))
            .collect::<Vec<_>>();
        let result = unsafe {
            ShellExecuteW(
                ptr::null_mut(),
                operation.as_ptr(),
                target.as_ptr(),
                ptr::null(),
                ptr::null(),
                SW_SHOWNORMAL,
            )
        };
        let result_code = result as isize;
        if result_code <= 32 {
            return Err(format!(
                "Windows could not open the URL (ShellExecuteW code {result_code})"
            ));
        }
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&validated)
            .spawn()
            .map_err(|err| format!("failed to open URL: {err}"))?;
        return Ok(());
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&validated)
            .spawn()
            .map_err(|err| format!("failed to open URL: {err}"))?;
        return Ok(());
    }
}

fn setup_app(app: &mut tauri::App) -> Result<(), Box<dyn Error>> {
    let fallback_log_dir = app
        .path()
        .local_data_dir()
        .ok()
        .map(|path| path.join("MediaProcessPipeline").join("logs"));
    app.manage(BackendProcess::default());
    app.manage(BootstrapController::new(fallback_log_dir));
    let bootstrap_url = packaged_bootstrap_navigation_url().map_err(boxed_error)?;
    let _window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(bootstrap_url))
        .on_navigation(trusted_app_navigation)
        .on_new_window(|url, _features| {
            let _ = open_external_url(url.to_string());
            NewWindowResponse::Deny
        })
        .title("MediaProcessPipeline")
        .inner_size(1440.0, 980.0)
        .min_inner_size(1024.0, 720.0)
        .build()
        .map_err(|err| boxed_error(format!("failed to create app window: {err}")))?;

    let app_handle = app.handle().clone();
    if let Err(error) = schedule_bootstrap_attempt(&app_handle) {
        let failure = bootstrap_internal_failure(error);
        let _ = set_bootstrap_failure(&app_handle, &failure);
    }

    Ok(())
}

fn packaged_app_navigation_url() -> Result<url::Url, String> {
    let origin = if cfg!(target_os = "windows") {
        "http://tauri.localhost"
    } else {
        "tauri://localhost"
    };
    url::Url::parse(&format!(
        "{origin}/index.html?appVersion={}&source={}&runtime={}",
        env!("CARGO_PKG_VERSION"),
        env!("MPP_BUILD_COMMIT"),
        env!("MPP_RUNTIME_MANIFEST_SHA256")
    ))
    .map_err(|error| format!("packaged app URL is invalid: {error}"))
}

fn packaged_bootstrap_navigation_url() -> Result<url::Url, String> {
    let mut url = packaged_app_navigation_url()?;
    url.query_pairs_mut().append_pair("bootstrap", "1");
    Ok(url)
}

fn trusted_app_navigation(url: &url::Url) -> bool {
    if url.as_str() == "about:blank" {
        return true;
    }
    let has_clean_authority =
        url.username().is_empty() && url.password().is_none() && url.port().is_none();
    let production_origin = has_clean_authority
        && ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
            || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost")));
    production_origin
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            backend_get_status,
            bootstrap_get_preflight,
            backend_get_logs,
            backend_start,
            backend_stop,
            backend_restart,
            backend_retry,
            open_logs,
            open_external_url
        ])
        .setup(setup_app)
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                stop_backend_on_close(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while running MediaProcessPipeline desktop shell");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            stop_backend_on_close(app_handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        net::TcpListener,
        sync::atomic::{AtomicU64, Ordering},
        sync::mpsc,
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_TEMP_ID: AtomicU64 = AtomicU64::new(0);

    struct TestDirectory {
        path: PathBuf,
    }

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("system clock should be after the Unix epoch")
                .as_nanos();
            let id = NEXT_TEMP_ID.fetch_add(1, Ordering::Relaxed);
            let path = env::temp_dir().join(format!(
                "mpp-tauri-{label}-{}-{nanos}-{id}",
                std::process::id()
            ));
            fs::create_dir_all(&path).expect("test directory should be created");
            Self { path }
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    fn write_file(path: &Path, content: &str) {
        write_bytes(path, content.as_bytes());
    }

    fn write_bytes(path: &Path, content: &[u8]) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("test file parent should be created");
        }
        fs::write(path, content).expect("test file should be written");
    }

    fn test_uv_binary() -> Vec<u8> {
        let mut bytes = vec![0_u8; 70];
        bytes[0..2].copy_from_slice(b"MZ");
        bytes[0x3c..0x40].copy_from_slice(&64_u32.to_le_bytes());
        bytes[64..68].copy_from_slice(b"PE\0\0");
        bytes[68..70].copy_from_slice(&0x8664_u16.to_le_bytes());
        bytes
    }

    fn write_installed_manifest(root: &Path) {
        write_file(&root.join(TOOL_CONTRACT_PATH), TRUSTED_TOOL_CONTRACT_JSON);
        write_file(
            &root.join("third-party-licenses/uv-LICENSE-MIT.txt"),
            "fixture MIT license",
        );
        write_bytes(&root.join("bin/uv.exe"), &test_uv_binary());

        let files = collect_runtime_files(root).expect("fixture runtime should scan");
        let records = files
            .iter()
            .map(|(path, absolute)| {
                let metadata = fs::metadata(absolute).expect("fixture metadata should exist");
                serde_json::json!({
                    "path": path,
                    "size": metadata.len(),
                    "sha256": file_sha256(absolute).expect("fixture hash should compute"),
                })
            })
            .collect::<Vec<_>>();
        let manifest = serde_json::json!({
            "schema": RUNTIME_MANIFEST_SCHEMA,
            "appVersion": env!("CARGO_PKG_VERSION"),
            "sourceCommit": env!("MPP_BUILD_COMMIT"),
            "sourceDirty": false,
            "toolContract": TOOL_CONTRACT_PATH,
            "uv": {
                "version": "0.9.21",
                "path": "bin/uv.exe",
                "sha256": "493a3a420f88fd28799ea5f61a39f89308d3bbbd7796bd98611367512b38dba9",
                "size": 70,
                "peMachine": "0x8664",
            },
            "files": records,
        });
        write_file(
            &root.join(RUNTIME_MANIFEST_FILE),
            &serde_json::to_string_pretty(&manifest).expect("fixture manifest should serialize"),
        );
    }

    fn create_runtime(root: &Path, installed: bool) {
        fs::create_dir_all(root.join("backend").join("app"))
            .expect("backend/app should be created");
        write_file(&root.join("backend/app/__init__.py"), "");
        write_file(&root.join("pyproject.toml"), "[project]\nname='mpp'\n");
        write_file(&root.join("uv.lock"), "version = 1\n");
        write_file(&root.join("VERSION"), env!("CARGO_PKG_VERSION"));
        write_file(&root.join("web/dist/index.html"), "<!doctype html>");
        if installed {
            write_installed_manifest(root);
        }
    }

    fn read_manifest(root: &Path) -> serde_json::Value {
        serde_json::from_slice(
            &fs::read(root.join(RUNTIME_MANIFEST_FILE)).expect("manifest should be readable"),
        )
        .expect("manifest should be valid JSON")
    }

    fn write_manifest(root: &Path, manifest: &serde_json::Value) {
        write_file(
            &root.join(RUNTIME_MANIFEST_FILE),
            &serde_json::to_string_pretty(manifest).expect("manifest should serialize"),
        );
    }

    fn health_response(status: u16, body: serde_json::Value) -> Vec<u8> {
        health_response_with_connection(status, body, "close")
    }

    fn health_response_with_connection(
        status: u16,
        body: serde_json::Value,
        connection: &str,
    ) -> Vec<u8> {
        let body = serde_json::to_vec(&body).expect("health fixture should serialize");
        let reason = if status == 200 { "OK" } else { "Error" };
        let mut response = format!(
            "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n\
             Content-Length: {}\r\nConnection: {connection}\r\n\r\n",
            body.len(),
        )
        .into_bytes();
        response.extend(body);
        response
    }

    fn compatible_health_body(session_secret: &str, nonce: &str) -> serde_json::Value {
        serde_json::json!({
            "status": "healthy",
            "product": HEALTH_PRODUCT,
            "protocol": HEALTH_PROTOCOL,
            "service": HEALTH_SERVICE,
            "version": env!("CARGO_PKG_VERSION"),
            "desktopProof": desktop_health_proof(session_secret, nonce)
                .expect("fixture health proof should compute"),
        })
    }

    fn request_header(request: &[u8], name: &str) -> Option<String> {
        let request = std::str::from_utf8(request).ok()?;
        request.lines().find_map(|line| {
            let (header, value) = line.split_once(':')?;
            header
                .eq_ignore_ascii_case(name)
                .then(|| value.trim().to_string())
        })
    }

    fn serve_health_once<F>(response: F) -> (SocketAddr, mpsc::Receiver<Vec<u8>>)
    where
        F: FnOnce(&[u8]) -> Vec<u8> + Send + 'static,
    {
        let listener = TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("listener should bind");
        let address = listener
            .local_addr()
            .expect("listener should have an address");
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("probe should connect");
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .expect("read timeout should apply");
            let mut request = Vec::new();
            let mut buffer = [0_u8; 1024];
            loop {
                let read = stream.read(&mut buffer).expect("request should read");
                if read == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..read]);
                if request.windows(4).any(|window| window == b"\r\n\r\n") {
                    break;
                }
            }
            let response = response(&request);
            sender.send(request).expect("request should be captured");
            stream
                .write_all(&response)
                .expect("response should be written");
        });
        (address, receiver)
    }

    fn arguments(spec: &BackendLaunchSpec) -> Vec<String> {
        spec.args
            .iter()
            .map(|value| value.to_string_lossy().into_owned())
            .collect()
    }

    fn proxy_test_layout(backend_port: u16) -> RuntimeLayout {
        RuntimeLayout {
            mode: RuntimeMode::Development,
            runtime_root: PathBuf::from("C:/mpp-test"),
            backend_dir: PathBuf::from("C:/mpp-test/backend"),
            web_dist_dir: PathBuf::from("C:/mpp-test/web/dist"),
            uv_executable: OsString::from("uv"),
            session_token: "ab".repeat(32),
            proxy_token: "cd".repeat(32),
            backend_port,
            user: UserPaths::new(PathBuf::from("C:/mpp-test-user")),
        }
    }

    fn prepare_test_request(
        request: impl Into<Vec<u8>>,
        layout: &RuntimeLayout,
    ) -> Result<ProxyRequestAction, ProxyFailure> {
        let request = request.into();
        let head_end = request
            .windows(4)
            .position(|window| window == b"\r\n\r\n")
            .map(|index| index + 4)
            .expect("test request should contain a complete header");
        prepare_proxy_request(request, head_end, layout)
    }

    fn tcp_pair() -> (TcpStream, TcpStream) {
        let listener =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("test TCP listener should bind");
        let address = listener
            .local_addr()
            .expect("test TCP listener should have an address");
        let client = TcpStream::connect(address).expect("test TCP client should connect");
        let (server, _) = listener.accept().expect("test TCP server should accept");
        (client, server)
    }

    fn serve_proxy_once(
        layout: RuntimeLayout,
    ) -> (TcpStream, mpsc::Receiver<Result<(), ProxyFailure>>) {
        let listener =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("proxy fixture should bind");
        let address = listener
            .local_addr()
            .expect("proxy fixture should have an address");
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let (stream, _) = listener.accept().expect("proxy fixture should accept");
            let result = handle_proxy_connection(stream, Arc::new(layout));
            sender
                .send(result)
                .expect("proxy result should be captured");
        });
        let client = TcpStream::connect(address).expect("proxy fixture client should connect");
        (client, receiver)
    }

    fn forwarded_request(action: ProxyRequestAction) -> PreparedProxyRequest {
        match action {
            ProxyRequestAction::Forward(request) => request,
            ProxyRequestAction::Preflight(_) => panic!("expected a forwarded proxy request"),
        }
    }

    #[test]
    fn explicit_project_has_highest_priority() {
        let temporary = TestDirectory::new("explicit-priority");
        let explicit = temporary.path.join("explicit");
        let portable = temporary.path.join("portable");
        let resources = temporary.path.join("resources");
        create_runtime(&explicit, false);
        create_runtime(&portable, false);
        create_runtime(&resources.join("runtime"), true);

        let result = resolve_runtime_candidate(
            &RuntimeCandidates {
                explicit_root: Some(explicit.clone()),
                executable: Some(portable.join("nested/bin/mpp-desktop.exe")),
                resource_dir: Some(resources),
                manifest_dir: None,
                allow_manifest_fallback: false,
            },
            RuntimeResolutionPolicy::Development,
        )
        .expect("explicit project should resolve");

        assert_eq!(result, (RuntimeMode::ExplicitProject, explicit));
    }

    #[test]
    fn portable_ancestor_precedes_installed_resources() {
        let temporary = TestDirectory::new("portable-priority");
        let portable = temporary.path.join("portable");
        let resources = temporary.path.join("resources");
        create_runtime(&portable, false);
        create_runtime(&resources.join("runtime"), true);

        let result = resolve_runtime_candidate(
            &RuntimeCandidates {
                explicit_root: None,
                executable: Some(portable.join("nested/bin/mpp-desktop.exe")),
                resource_dir: Some(resources),
                manifest_dir: None,
                allow_manifest_fallback: false,
            },
            RuntimeResolutionPolicy::Development,
        )
        .expect("portable ancestor should resolve");

        assert_eq!(result, (RuntimeMode::Portable, portable));
    }

    #[test]
    fn installed_runtime_supports_spaces_and_unicode() {
        let temporary = TestDirectory::new("installed-unicode");
        let resources = temporary.path.join("安装 Resources With Spaces");
        let installed = resources.join("runtime");
        create_runtime(&installed, true);

        let result = resolve_runtime_candidate(
            &RuntimeCandidates {
                explicit_root: None,
                executable: None,
                resource_dir: Some(resources),
                manifest_dir: None,
                allow_manifest_fallback: false,
            },
            RuntimeResolutionPolicy::Development,
        )
        .expect("installed resources should resolve");

        assert_eq!(result, (RuntimeMode::Installed, installed));
    }

    #[test]
    fn production_policy_uses_only_the_signed_resource_runtime() {
        let temporary = TestDirectory::new("production-runtime");
        let portable = temporary.path.join("portable");
        let resources = temporary.path.join("resources");
        let installed = resources.join("runtime");
        create_runtime(&portable, false);
        create_runtime(&installed, true);

        let result = resolve_runtime_candidate(
            &RuntimeCandidates {
                executable: Some(portable.join("mpp-desktop.exe")),
                resource_dir: Some(resources),
                manifest_dir: Some(portable.join("web/src-tauri")),
                allow_manifest_fallback: true,
                ..RuntimeCandidates::default()
            },
            RuntimeResolutionPolicy::Production,
        )
        .expect("production should resolve its signed resource runtime");

        assert_eq!(result, (RuntimeMode::Installed, installed));
    }

    #[test]
    fn production_policy_rejects_project_and_tool_overrides() {
        let temporary = TestDirectory::new("production-overrides");
        let explicit = temporary.path.join("explicit");
        let resources = temporary.path.join("resources");
        create_runtime(&explicit, false);
        create_runtime(&resources.join("runtime"), true);

        let root_error = resolve_runtime_candidate(
            &RuntimeCandidates {
                explicit_root: Some(explicit),
                resource_dir: Some(resources),
                ..RuntimeCandidates::default()
            },
            RuntimeResolutionPolicy::Production,
        )
        .expect_err("production must reject MPP_PROJECT_ROOT");
        assert!(root_error.contains("MPP_PROJECT_ROOT"));

        let uv = OsString::from("attacker-uv.exe");
        let user_root = temporary.path.join("user");
        assert!(validate_release_environment_overrides(
            RuntimeResolutionPolicy::Production,
            Some(&uv),
            None,
        )
        .expect_err("production must reject MPP_UV")
        .contains("MPP_UV"));
        assert!(validate_release_environment_overrides(
            RuntimeResolutionPolicy::Production,
            None,
            Some(&user_root),
        )
        .expect_err("production must reject MPP_USER_ROOT")
        .contains("MPP_USER_ROOT"));
    }

    #[test]
    fn production_policy_requires_a_resource_runtime() {
        let temporary = TestDirectory::new("production-resource-required");
        let portable = temporary.path.join("portable");
        create_runtime(&portable, false);

        let error = resolve_runtime_candidate(
            &RuntimeCandidates {
                executable: Some(portable.join("mpp-desktop.exe")),
                manifest_dir: Some(portable.join("web/src-tauri")),
                allow_manifest_fallback: true,
                ..RuntimeCandidates::default()
            },
            RuntimeResolutionPolicy::Production,
        )
        .expect_err("production must not fall back to source or portable runtimes");
        assert!(error.contains("signed Tauri resource directory"));
    }

    #[test]
    fn debug_manifest_is_the_last_fallback() {
        let temporary = TestDirectory::new("manifest-fallback");
        let root = temporary.path.join("source");
        let manifest = root.join("web/src-tauri");
        create_runtime(&root, false);
        fs::create_dir_all(&manifest).expect("manifest directory should be created");

        let result = resolve_runtime_candidate(
            &RuntimeCandidates {
                explicit_root: None,
                executable: None,
                resource_dir: Some(temporary.path.join("empty-resources")),
                manifest_dir: Some(manifest),
                allow_manifest_fallback: true,
            },
            RuntimeResolutionPolicy::Development,
        )
        .expect("manifest fallback should resolve");

        assert_eq!(result, (RuntimeMode::Development, root));
    }

    #[test]
    fn incomplete_explicit_runtime_reports_all_required_items() {
        let temporary = TestDirectory::new("incomplete-explicit");
        fs::create_dir_all(temporary.path.join("backend/app"))
            .expect("partial backend should be created");

        let error = resolve_runtime_candidate(
            &RuntimeCandidates {
                explicit_root: Some(temporary.path.clone()),
                ..RuntimeCandidates::default()
            },
            RuntimeResolutionPolicy::Development,
        )
        .expect_err("incomplete explicit runtime should fail");

        assert!(error.contains("pyproject.toml"));
        assert!(error.contains("uv.lock"));
        assert!(error.contains("VERSION"));
        assert!(error.contains("web/dist/index.html"));
    }

    #[test]
    fn installed_runtime_requires_bundled_uv() {
        let temporary = TestDirectory::new("missing-installed-uv");
        let resources = temporary.path.join("resources");
        create_runtime(&resources.join("runtime"), true);
        fs::remove_file(resources.join("runtime/bin/uv.exe"))
            .expect("fixture uv should be removed");

        let error = resolve_runtime_candidate(
            &RuntimeCandidates {
                resource_dir: Some(resources),
                ..RuntimeCandidates::default()
            },
            RuntimeResolutionPolicy::Development,
        )
        .expect_err("installed runtime without uv should fail");

        assert!(error.contains("bin/uv.exe"));
    }

    #[test]
    fn user_layout_creates_only_the_contract_directories() {
        let temporary = TestDirectory::new("user-layout");
        let root = temporary
            .path
            .join("Local Data With Spaces")
            .join("应用数据");
        let paths = resolve_user_paths(Some(root.clone()), temporary.path.clone())
            .expect("absolute user root should resolve");

        ensure_user_directories(&paths).expect("user directories should be created");

        assert_eq!(paths.root, root);
        assert_eq!(paths.config_file, root.join("config").join("config.json"));
        assert_eq!(paths.venv_dir, root.join("runtime").join(".venv"));
        assert_eq!(paths.python_dir, root.join("runtime").join("python"));
        for directory in paths.required_directories() {
            assert!(directory.is_dir(), "{} should exist", directory.display());
        }
        assert_eq!(
            fs::read_dir(&paths.state_dir)
                .expect("state directory should be readable")
                .count(),
            0,
            "write probe should be removed"
        );
    }

    #[test]
    fn user_layout_rejects_a_file_in_its_directory_chain() {
        let temporary = TestDirectory::new("user-layout-file");
        let root_file = temporary.path.join("occupied-root");
        write_file(&root_file, "not a directory");
        let paths = UserPaths::new(root_file);

        let error = ensure_user_directories(&paths)
            .expect_err("a regular file cannot serve as the user data root");
        assert!(error.contains("failed to create") || error.contains("real directory"));
    }

    #[cfg(windows)]
    #[test]
    fn user_layout_rejects_a_windows_junction() {
        let temporary = TestDirectory::new("user-layout-junction");
        let target = temporary.path.join("target");
        let junction = temporary.path.join("junction");
        fs::create_dir_all(&target).expect("junction target should be created");
        let output = Command::new("cmd")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&target)
            .output()
            .expect("cmd should create a test junction");
        assert!(
            output.status.success(),
            "junction fixture failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );

        let error = ensure_user_directories(&UserPaths::new(junction.clone()))
            .expect_err("user data root junctions must be rejected");
        assert!(error.contains("reparse point") || error.contains("junction"));
        fs::remove_dir(&junction).expect("test junction should be removed");
    }

    #[test]
    fn relative_user_root_is_rejected() {
        let temporary = TestDirectory::new("relative-user-root");
        let error = resolve_user_paths(
            Some(PathBuf::from("relative/user/root")),
            temporary.path.clone(),
        )
        .expect_err("relative MPP_USER_ROOT should fail");
        assert!(error.contains("absolute path"));
    }

    #[test]
    fn default_user_root_is_scoped_below_local_data() {
        let temporary = TestDirectory::new("default-user-root");
        let local_data = temporary.path.join("Local");
        let paths =
            resolve_user_paths(None, local_data.clone()).expect("default user root should resolve");

        assert_eq!(paths.root, local_data.join("MediaProcessPipeline"));
    }

    #[test]
    fn installed_launch_uses_bundled_uv_frozen_project_and_user_env() {
        let temporary = TestDirectory::new("installed-command");
        let runtime_root = temporary.path.join("Program Files/MPP/resources/runtime");
        let user_root = temporary.path.join("Local AppData/MediaProcessPipeline");
        create_runtime(&runtime_root, true);
        let layout = runtime_layout_from_parts(
            RuntimeMode::Installed,
            runtime_root.clone(),
            UserPaths::new(user_root.clone()),
            Some(OsString::from("ignored-system-uv")),
            "ab".repeat(32),
            "bc".repeat(32),
            43123,
        );

        let spec = build_backend_launch_spec(&layout);
        let args = arguments(&spec);

        assert!(spec.clear_environment);
        assert_eq!(
            PathBuf::from(&spec.program),
            runtime_root.join("bin/uv.exe")
        );
        assert_eq!(spec.cwd, runtime_root.join("backend"));
        assert_eq!(
            &args[..4],
            &[
                "run",
                "--frozen",
                "--project",
                runtime_root.to_string_lossy().as_ref()
            ]
        );
        let python_index = args
            .iter()
            .position(|argument| argument == "python")
            .expect("launch should invoke Python");
        assert_eq!(
            &args[python_index..python_index + 4],
            &["python", "-E", "-s", "-u"]
        );
        assert!(args.iter().any(|argument| argument == "--desktop-loopback"));
        assert_eq!(
            spec.env.get("MPP_CONFIG_FILE"),
            Some(&path_env(&user_root.join("config").join("config.json")))
        );
        assert_eq!(
            spec.env.get("MPP_WEB_DIST_DIR"),
            Some(&path_env(&runtime_root.join("web").join("dist")))
        );
        assert_eq!(
            spec.env.get("MPP_LOG_DIR"),
            Some(&path_env(&user_root.join("logs")))
        );
        assert_eq!(
            spec.env.get("MPP_CACHE_DIR"),
            Some(&path_env(&user_root.join("cache")))
        );
        assert_eq!(
            spec.env.get("MPP_DATA_ROOT"),
            Some(&path_env(&user_root.join("data")))
        );
        assert_eq!(
            spec.env.get("UV_PROJECT_ENVIRONMENT"),
            Some(&path_env(&user_root.join("runtime").join(".venv")))
        );
        assert_eq!(
            spec.env.get("UV_CACHE_DIR"),
            Some(&path_env(&user_root.join("cache").join("uv")))
        );
        assert_eq!(
            spec.env.get("UV_PYTHON_INSTALL_DIR"),
            Some(&path_env(&user_root.join("runtime").join("python")))
        );
        assert_eq!(
            spec.env.get("UV_MANAGED_PYTHON"),
            Some(&OsString::from("1"))
        );
        assert_eq!(
            spec.env.get("HF_HOME"),
            Some(&path_env(&user_root.join("cache").join("huggingface")))
        );
        assert_eq!(
            spec.env.get("TORCH_HOME"),
            Some(&path_env(&user_root.join("cache").join("torch")))
        );
        assert_eq!(
            spec.env.get("PLAYWRIGHT_BROWSERS_PATH"),
            Some(&path_env(&user_root.join("cache").join("ms-playwright")))
        );
        assert_eq!(
            spec.env.get("MPP_DESKTOP_SESSION_TOKEN"),
            Some(&OsString::from("ab".repeat(32)))
        );
        assert_eq!(
            spec.env.get("PYTHONDONTWRITEBYTECODE"),
            Some(&OsString::from("1"))
        );
        assert_eq!(spec.env.get("PYTHONNOUSERSITE"), Some(&OsString::from("1")));
        assert_eq!(spec.env.get("PYTHONSAFEPATH"), Some(&OsString::from("1")));
        assert_eq!(
            spec.env.get("TEMP"),
            Some(&path_env(&user_root.join("runtime").join("tmp")))
        );
        assert_eq!(
            spec.env.get("TMP"),
            Some(&path_env(&user_root.join("runtime").join("tmp")))
        );
        assert_eq!(spec.env.get("UV_NO_CONFIG"), Some(&OsString::from("1")));
    }

    #[test]
    fn installed_environment_inherits_only_the_explicit_system_allowlist() {
        let inherited = safe_inherited_environment([
            (OsString::from("Path"), OsString::from("safe-path")),
            (OsString::from("systemroot"), OsString::from("C:\\Windows")),
            (
                OsString::from("PYTHONPATH"),
                OsString::from("C:\\attacker-python"),
            ),
            (
                OsString::from("PYTHONHOME"),
                OsString::from("C:\\attacker-home"),
            ),
            (
                OsString::from("VIRTUAL_ENV"),
                OsString::from("C:\\attacker-venv"),
            ),
            (
                OsString::from("UV_CONFIG_FILE"),
                OsString::from("C:\\attacker-uv.toml"),
            ),
            (
                OsString::from("MPP_PROJECT_ROOT"),
                OsString::from("C:\\attacker-runtime"),
            ),
        ]);

        assert_eq!(
            inherited,
            BTreeMap::from([
                ("PATH".to_string(), OsString::from("safe-path")),
                ("SYSTEMROOT".to_string(), OsString::from("C:\\Windows")),
            ])
        );
    }

    #[test]
    fn portable_launch_keeps_configured_uv_and_project_behavior() {
        let temporary = TestDirectory::new("portable-command");
        let runtime_root = temporary.path.join("portable");
        create_runtime(&runtime_root, false);
        let layout = runtime_layout_from_parts(
            RuntimeMode::Portable,
            runtime_root.clone(),
            UserPaths::new(temporary.path.join("user")),
            Some(OsString::from("custom-uv.exe")),
            "cd".repeat(32),
            "de".repeat(32),
            43124,
        );

        let spec = build_backend_launch_spec(&layout);
        let args = arguments(&spec);

        assert!(!spec.clear_environment);
        assert_eq!(spec.program, OsString::from("custom-uv.exe"));
        assert_eq!(args.first().map(String::as_str), Some("run"));
        assert!(!args.iter().any(|arg| arg == "--frozen"));
        assert_eq!(
            args.get(2).map(String::as_str),
            Some(runtime_root.to_string_lossy().as_ref())
        );
        assert!(!spec.env.contains_key("MPP_CONFIG_FILE"));
        assert!(!spec.env.contains_key("UV_PROJECT_ENVIRONMENT"));
    }

    #[test]
    fn installed_manifest_rejects_tampered_file() {
        let temporary = TestDirectory::new("manifest-tamper");
        create_runtime(&temporary.path, true);
        let index = temporary.path.join("web/dist/index.html");
        let original_size = fs::metadata(&index)
            .expect("fixture index should exist")
            .len() as usize;
        write_bytes(&index, &vec![b'X'; original_size]);

        let error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("tampered runtime should be rejected");
        assert!(error.contains("SHA-256 mismatch"));
    }

    #[test]
    fn installed_runtime_is_revalidated_immediately_before_every_spawn() {
        let temporary = TestDirectory::new("spawn-revalidation");
        create_runtime(&temporary.path, true);
        let layout = runtime_layout_from_parts(
            RuntimeMode::Installed,
            temporary.path.clone(),
            UserPaths::new(temporary.path.join("user")),
            None,
            "ab".repeat(32),
            "cd".repeat(32),
            43125,
        );
        revalidate_runtime_before_spawn(&layout)
            .expect("untampered installed runtime should pass spawn validation");

        write_file(
            &temporary.path.join("backend/app/__init__.py"),
            "tampered after startup validation",
        );
        let error = revalidate_runtime_before_spawn(&layout)
            .expect_err("tampering between launches must block the next spawn");
        assert!(error.contains("revalidation failed before spawn"));
        assert!(error.contains("SHA-256 mismatch") || error.contains("size mismatch"));
    }

    #[test]
    fn installed_manifest_rejects_extra_file() {
        let temporary = TestDirectory::new("manifest-extra");
        create_runtime(&temporary.path, true);
        write_file(&temporary.path.join("unexpected.txt"), "unexpected");

        let error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("extra runtime file should be rejected");
        assert!(error.contains("unlisted runtime files"));
        assert!(error.contains("unexpected.txt"));
    }

    #[test]
    fn installed_manifest_rejects_dirty_source() {
        let temporary = TestDirectory::new("manifest-dirty");
        create_runtime(&temporary.path, true);
        let mut manifest = read_manifest(&temporary.path);
        manifest["sourceDirty"] = serde_json::Value::Bool(true);
        write_manifest(&temporary.path, &manifest);

        let error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("dirty source runtime should be rejected");
        assert!(error.contains("sourceDirty must be false"));
    }

    #[test]
    fn installed_manifest_rejects_wrong_commit() {
        let temporary = TestDirectory::new("manifest-commit");
        create_runtime(&temporary.path, true);
        let mut manifest = read_manifest(&temporary.path);
        manifest["sourceCommit"] = serde_json::Value::String("0".repeat(40));
        write_manifest(&temporary.path, &manifest);

        let error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("wrong commit runtime should be rejected");
        assert!(error.contains("differs from desktop build"));
    }

    #[test]
    fn installed_manifest_rejects_wrong_version() {
        let temporary = TestDirectory::new("manifest-version");
        create_runtime(&temporary.path, true);
        let mut manifest = read_manifest(&temporary.path);
        manifest["appVersion"] = serde_json::Value::String("99.0.0".to_string());
        write_manifest(&temporary.path, &manifest);

        let error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("wrong version runtime should be rejected");
        assert!(error.contains("differs from desktop"));
    }

    #[test]
    fn installed_manifest_rejects_path_traversal_and_duplicates() {
        let temporary = TestDirectory::new("manifest-path");
        create_runtime(&temporary.path, true);
        let original = read_manifest(&temporary.path);
        let mut duplicate_manifest = original.clone();
        let first = duplicate_manifest["files"][0].clone();
        duplicate_manifest["files"]
            .as_array_mut()
            .expect("files should be an array")
            .push(first);
        write_manifest(&temporary.path, &duplicate_manifest);
        let duplicate_error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("duplicate manifest path should be rejected");
        assert!(duplicate_error.contains("duplicate manifest path"));

        let mut traversal_manifest = original;
        traversal_manifest["files"][0]["path"] =
            serde_json::Value::String("../outside".to_string());
        write_manifest(&temporary.path, &traversal_manifest);
        let traversal_error = validate_installed_runtime_manifest(&temporary.path)
            .expect_err("manifest traversal should be rejected");
        assert!(traversal_error.contains("invalid manifest path"));
    }

    #[test]
    fn installed_manifest_rejects_manifest_bytes_outside_the_build_trust_root() {
        let temporary = TestDirectory::new("manifest-trust-root");
        create_runtime(&temporary.path, true);
        let trusted_hash = file_sha256(&temporary.path.join(RUNTIME_MANIFEST_FILE))
            .expect("fixture manifest hash should compute");
        let mut bytes = fs::read(temporary.path.join(RUNTIME_MANIFEST_FILE))
            .expect("fixture manifest should read");
        bytes.push(b'\n');
        write_bytes(&temporary.path.join(RUNTIME_MANIFEST_FILE), &bytes);

        let error = validate_installed_runtime_manifest_with_hash(&temporary.path, &trusted_hash)
            .expect_err("changed manifest bytes should be rejected");
        assert!(error.contains("SHA-256 differs from the desktop build"));
    }

    #[test]
    fn installed_manifest_rejects_payload_tampering_with_a_resigned_manifest() {
        let temporary = TestDirectory::new("manifest-resigned");
        create_runtime(&temporary.path, true);
        let trusted_hash = file_sha256(&temporary.path.join(RUNTIME_MANIFEST_FILE))
            .expect("fixture manifest hash should compute");

        let target_relative = "backend/app/__init__.py";
        let target = temporary.path.join(target_relative);
        write_file(&target, "tampered and re-signed");
        let mut manifest = read_manifest(&temporary.path);
        let record = manifest["files"]
            .as_array_mut()
            .expect("files should be an array")
            .iter_mut()
            .find(|record| record["path"] == target_relative)
            .expect("target should have a manifest record");
        record["size"] = serde_json::json!(fs::metadata(&target)
            .expect("tampered file should exist")
            .len());
        record["sha256"] = serde_json::Value::String(
            file_sha256(&target).expect("tampered file hash should compute"),
        );
        write_manifest(&temporary.path, &manifest);

        let error = validate_installed_runtime_manifest_with_hash(&temporary.path, &trusted_hash)
            .expect_err("self-consistent but untrusted manifest should be rejected");
        assert!(error.contains("SHA-256 differs from the desktop build"));
    }

    #[test]
    fn installed_manifest_rejects_the_development_hash_sentinel() {
        let temporary = TestDirectory::new("manifest-sentinel");
        create_runtime(&temporary.path, true);

        let error = validate_installed_runtime_manifest_with_hash(
            &temporary.path,
            "development-no-runtime-manifest",
        )
        .expect_err("development sentinel must not authorize an installed runtime");
        assert!(error.contains("without a trusted installed runtime manifest hash"));
    }

    #[test]
    fn stale_backend_waiters_cannot_match_a_restarted_generation() {
        assert!(generation_is_current(7, Some(7), 7));
        assert!(!generation_is_current(8, Some(8), 7));
        assert!(!generation_is_current(7, Some(8), 7));
        assert!(!generation_is_current(7, None, 7));
        assert_eq!(next_lifecycle_generation(u64::MAX), 1);
    }

    #[test]
    fn preflight_report_is_ordered_and_uses_stable_overall_precedence() {
        let report = finalize_preflight_report(vec![
            preflight_component(
                "webview2", "WebView2", "ready", true, None, None, None, None, None,
            ),
            preflight_component(
                "runtime-settings",
                "Runtime Settings",
                "invalid",
                true,
                None,
                None,
                Some("CONFIG_INVALID"),
                Some("Correct the settings file."),
                Some("invalid JSON".to_string()),
            ),
            preflight_component(
                "desktop-runtime",
                "Desktop Runtime",
                "ready",
                true,
                Some("0.4.1".to_string()),
                None,
                None,
                None,
                None,
            ),
        ]);

        assert_eq!(report.schema_version, 1);
        assert_eq!(report.overall_status, "needs_repair");
        assert_eq!(
            report
                .components
                .iter()
                .map(|component| component.component_id.as_str())
                .collect::<Vec<_>>(),
            ["desktop-runtime", "runtime-settings", "webview2"]
        );
        let serialized = serde_json::to_value(&report).expect("preflight should serialize");
        assert!(serialized["components"][0]["path"].is_null());
        assert!(serialized["components"][0]["error_code"].is_null());

        let blocked = finalize_preflight_report(vec![preflight_component(
            "backend-private-port",
            "Backend Private Port",
            "blocked",
            true,
            None,
            None,
            Some("PRIVATE_PORT_IN_USE"),
            None,
            None,
        )]);
        assert_eq!(blocked.overall_status, "blocked");
    }

    #[test]
    fn default_preflight_matches_the_shared_scanning_contract() {
        let report = BootstrapPreflightReport::default();
        assert_eq!(report.overall_status, "scanning");
        assert_eq!(
            report
                .components
                .iter()
                .map(|component| component.component_id.as_str())
                .collect::<Vec<_>>(),
            PREFLIGHT_COMPONENT_ORDER
        );
        assert!(report
            .components
            .iter()
            .all(|component| component.status == "scanning"));

        let expected: serde_json::Value = serde_json::from_str(include_str!(
            "../../src/lib/fixtures/bootstrap-preflight-scanning-v1.json"
        ))
        .expect("shared scanning fixture should be valid JSON");
        let actual = serde_json::to_value(report).expect("default preflight should serialize");
        assert_eq!(actual, expected);
    }

    #[test]
    fn preflight_fields_are_utf8_safe_and_bounded() {
        let long_detail = "界".repeat(MAX_PREFLIGHT_DETAIL_BYTES);
        let long_path = PathBuf::from(format!("C:/{}", "路".repeat(MAX_PREFLIGHT_PATH_BYTES)));
        let component = preflight_component(
            "runtime-settings",
            "Runtime Settings",
            "invalid",
            true,
            Some("v".repeat(512)),
            Some(&long_path),
            Some("CONFIG_INVALID"),
            Some(&long_detail),
            Some(long_detail.clone()),
        );

        assert!(component.version.expect("version should exist").len() <= 128);
        assert!(component.path.expect("path should exist").len() <= MAX_PREFLIGHT_PATH_BYTES);
        assert!(
            component
                .remediation
                .expect("remediation should exist")
                .len()
                <= MAX_PREFLIGHT_DETAIL_BYTES
        );
        assert!(component.detail.expect("detail should exist").len() <= MAX_PREFLIGHT_DETAIL_BYTES);

        let (retained, truncated) =
            read_bounded_probe_stream(io::Cursor::new(vec![b'x'; MAX_PREFLIGHT_OUTPUT_BYTES * 4]))
                .expect("bounded probe output should be readable");
        assert_eq!(retained.len(), MAX_PREFLIGHT_OUTPUT_BYTES);
        assert!(truncated);
    }

    #[test]
    fn invalid_settings_have_a_stable_config_error_without_mutation() {
        let temporary = TestDirectory::new("preflight-invalid-settings");
        let user = UserPaths::new(temporary.path.join("user"));
        fs::create_dir_all(&user.config_dir).expect("config directory should be created");
        write_file(&user.config_file, "{ invalid");
        let mut layout = proxy_test_layout(43122);
        layout.mode = RuntimeMode::Installed;
        layout.user = user;

        let before = fs::read(&layout.user.config_file).expect("fixture should be readable");
        let component = probe_runtime_settings_component(&layout);
        let after = fs::read(&layout.user.config_file).expect("fixture should remain readable");

        assert_eq!(component.status, "invalid");
        assert!(component.required);
        assert_eq!(component.error_code.as_deref(), Some("CONFIG_INVALID"));
        assert_eq!(before, after);
    }

    #[test]
    fn python_environment_probe_is_read_only_and_missing_is_stable() {
        let temporary = TestDirectory::new("preflight-python-environment");
        let mut layout = proxy_test_layout(43122);
        layout.mode = RuntimeMode::Installed;
        layout.user = UserPaths::new(temporary.path.join("user"));

        assert!(!layout.user.venv_dir.exists());
        let component = probe_python_environment_component(&layout);
        assert!(!layout.user.venv_dir.exists());
        assert_eq!(component.status, "missing");
        assert_eq!(
            component.error_code.as_deref(),
            Some("PYTHON_ENVIRONMENT_MISSING")
        );
    }

    #[test]
    fn preflight_error_classification_maps_required_failures_to_bootstrap() {
        let report = finalize_preflight_report(vec![preflight_component(
            "ffmpeg",
            "FFmpeg",
            "missing",
            true,
            None,
            None,
            Some("FFMPEG_MISSING"),
            Some("Install FFmpeg."),
            Some("ffmpeg.exe was not found".to_string()),
        )]);
        let failure =
            preflight_blocking_failure(&report).expect("required missing tool should block");
        assert_eq!(failure.error_code, "FFMPEG_MISSING");
        assert_eq!(failure.component_id, "ffmpeg");
        assert!(!failure.retryable);

        assert_eq!(
            tool_probe_failure_mapping("ffprobe", ProbeCommandFailureKind::Timeout).1,
            "FFPROBE_PROBE_TIMEOUT"
        );
        assert_eq!(
            tool_probe_failure_mapping("ffmpeg", ProbeCommandFailureKind::PermissionDenied).1,
            "FFMPEG_EXECUTION_BLOCKED"
        );

        let uv_failure = runtime_resolution_failure(
            "manifest-declared runtime files are missing: bin/uv.exe".to_string(),
            Some(Path::new("C:/Program Files/MPP/runtime")),
        );
        assert_eq!(uv_failure.error_code, "UV_MISSING");
        assert_eq!(uv_failure.component_id, "bundled-uv");
        assert_eq!(
            runtime_resolution_failure(
                "runtime root is incomplete: missing uv.lock".to_string(),
                None,
            )
            .error_code,
            "RUNTIME_INVALID"
        );

        let early_report = preflight_report_from_failure(&uv_failure);
        assert_eq!(
            early_report.components.len(),
            PREFLIGHT_COMPONENT_ORDER.len()
        );
        assert_eq!(
            early_report
                .components
                .iter()
                .map(|component| component.component_id.as_str())
                .collect::<Vec<_>>(),
            PREFLIGHT_COMPONENT_ORDER
        );
        assert_eq!(
            early_report
                .components
                .iter()
                .find(|component| component.component_id == "bundled-uv")
                .and_then(|component| component.error_code.as_deref()),
            Some("UV_MISSING")
        );

        let mut raced_port_report = finalize_preflight_report(vec![preflight_component(
            "desktop-proxy-port",
            "Desktop Proxy Port",
            "ready",
            true,
            None,
            None,
            None,
            None,
            None,
        )]);
        let raced_port_failure = BootstrapFailure::retryable(
            "PORT_IN_USE",
            "desktop-proxy",
            "Close the conflicting process and retry.",
            "localhost port 18000 was claimed after preflight",
            None,
        );
        apply_failure_to_preflight(&mut raced_port_report, &raced_port_failure);
        assert_eq!(raced_port_report.overall_status, "blocked");
        assert_eq!(
            raced_port_report.components[0].error_code.as_deref(),
            Some("PORT_IN_USE")
        );
    }

    #[test]
    fn non_contract_failure_components_stay_within_the_fixed_preflight_set() {
        let cases = [
            (
                BootstrapFailure::manual(
                    "BACKEND_HEALTH_INVALID",
                    "backend-health",
                    "Repair the runtime.",
                    "health contract mismatch",
                    None,
                ),
                "python-environment",
            ),
            (
                bootstrap_internal_failure("bootstrap runtime lock poisoned"),
                "desktop-runtime",
            ),
            (
                BootstrapFailure::retryable(
                    "DESKTOP_SESSION_INIT_FAILED",
                    "desktop-session",
                    "Restart the application.",
                    "session entropy source unavailable",
                    None,
                ),
                "desktop-runtime",
            ),
        ];

        for (failure, expected_component_id) in cases {
            let report = preflight_report_from_failure(&failure);
            assert_eq!(report.components.len(), PREFLIGHT_COMPONENT_ORDER.len());
            assert_eq!(
                report
                    .components
                    .iter()
                    .map(|component| component.component_id.as_str())
                    .collect::<Vec<_>>(),
                PREFLIGHT_COMPONENT_ORDER
            );
            assert_eq!(
                report
                    .components
                    .iter()
                    .find(|component| component.component_id == expected_component_id)
                    .and_then(|component| component.error_code.as_deref()),
                Some(failure.error_code.as_str())
            );
        }
    }

    #[test]
    fn python_version_probe_accepts_only_supported_fixed_signatures() {
        for version in ["3.11.0", "3.11.10", "3.12.9"] {
            assert_eq!(
                classify_python_version_output(&ProbeCommandOutput {
                    success: true,
                    output: format!("Python {version}"),
                }),
                PythonVersionOutcome::Supported(version.to_string())
            );
        }
        for version in ["2.7.18", "3.10.14", "3.13.0"] {
            assert_eq!(
                classify_python_version_output(&ProbeCommandOutput {
                    success: true,
                    output: format!("Python {version}"),
                }),
                PythonVersionOutcome::Unsupported(version.to_string())
            );
        }
        for output in [
            ProbeCommandOutput {
                success: false,
                output: "Python 3.11.10".to_string(),
            },
            ProbeCommandOutput {
                success: true,
                output: "Python 3.11".to_string(),
            },
            ProbeCommandOutput {
                success: true,
                output: "Python 3.11.0rc1".to_string(),
            },
            ProbeCommandOutput {
                success: true,
                output: "python 3.11.10".to_string(),
            },
            ProbeCommandOutput {
                success: true,
                output: "Python 3.11.10 extra".to_string(),
            },
            ProbeCommandOutput {
                success: true,
                output: "arbitrary executable output".to_string(),
            },
        ] {
            assert_eq!(
                classify_python_version_output(&output),
                PythonVersionOutcome::Invalid
            );
        }

        assert_eq!(
            python_probe_failure_mapping(ProbeCommandFailureKind::Timeout).1,
            "PYTHON_PROBE_TIMEOUT"
        );
        assert_eq!(
            python_probe_failure_mapping(ProbeCommandFailureKind::PermissionDenied).1,
            "PYTHON_EXECUTION_BLOCKED"
        );
        assert_eq!(
            python_probe_failure_mapping(ProbeCommandFailureKind::Supervision).1,
            "PYTHON_PROBE_BLOCKED"
        );
        assert_eq!(
            python_probe_failure_mapping(ProbeCommandFailureKind::Spawn).1,
            "PYTHON_ENVIRONMENT_INVALID"
        );
    }

    #[test]
    fn media_tool_probe_requires_the_matching_fixed_version_signature() {
        let ffmpeg = ProbeCommandOutput {
            success: true,
            output: "ffmpeg version 7.1.1-full_build Copyright FFmpeg".to_string(),
        };
        let ffprobe = ProbeCommandOutput {
            success: true,
            output: "ffprobe version 7.1.1-full_build Copyright FFmpeg".to_string(),
        };
        assert_eq!(
            validated_media_tool_version_line("ffmpeg", &ffmpeg).as_deref(),
            Some("ffmpeg version 7.1.1-full_build Copyright FFmpeg")
        );
        assert_eq!(
            validated_media_tool_version_line("ffprobe", &ffprobe).as_deref(),
            Some("ffprobe version 7.1.1-full_build Copyright FFmpeg")
        );

        for (tool, output) in [
            ("ffmpeg", "ffprobe version 7.1.1"),
            ("ffprobe", "ffmpeg version 7.1.1"),
            ("ffmpeg", "arbitrary executable output"),
            ("ffprobe", "ffprobe version "),
        ] {
            assert!(validated_media_tool_version_line(
                tool,
                &ProbeCommandOutput {
                    success: true,
                    output: output.to_string(),
                }
            )
            .is_none());
        }
        assert!(validated_media_tool_version_line(
            "ffmpeg",
            &ProbeCommandOutput {
                success: false,
                output: "ffmpeg version 7.1.1".to_string(),
            }
        )
        .is_none());
    }

    #[test]
    fn settings_preflight_accepts_only_the_fixed_credential_safe_contract() {
        assert_eq!(
            classify_settings_preflight_output(&ProbeCommandOutput {
                success: true,
                output: SETTINGS_PREFLIGHT_OK_TOKEN.to_string(),
            }),
            SettingsPreflightOutcome::Valid
        );
        assert_eq!(
            classify_settings_preflight_output(&ProbeCommandOutput {
                success: false,
                output: SETTINGS_PREFLIGHT_INVALID_TOKEN.to_string(),
            }),
            SettingsPreflightOutcome::Invalid
        );
        for output in [
            ProbeCommandOutput {
                success: true,
                output: SETTINGS_PREFLIGHT_INVALID_TOKEN.to_string(),
            },
            ProbeCommandOutput {
                success: false,
                output: SETTINGS_PREFLIGHT_OK_TOKEN.to_string(),
            },
            ProbeCommandOutput {
                success: false,
                output: "secret-bearing validation error".to_string(),
            },
        ] {
            assert_eq!(
                classify_settings_preflight_output(&output),
                SettingsPreflightOutcome::Error
            );
        }
    }

    #[test]
    fn data_root_preflight_rechecks_writability_and_cleans_probes() {
        let temporary = TestDirectory::new("preflight-data-root");
        let mut layout = proxy_test_layout(43122);
        layout.mode = RuntimeMode::Installed;
        layout.user = UserPaths::new(temporary.path.join("user"));
        for directory in
            std::iter::once(layout.user.root.as_path()).chain(layout.user.required_directories())
        {
            fs::create_dir_all(directory).expect("data directory should be created");
        }

        let component = probe_data_root_component(&layout);
        assert_eq!(component.status, "ready");
        for directory in
            std::iter::once(layout.user.root.as_path()).chain(layout.user.required_directories())
        {
            assert!(
                fs::read_dir(directory)
                    .expect("data directory should be readable")
                    .all(|entry| !entry
                        .expect("directory entry should be readable")
                        .file_name()
                        .to_string_lossy()
                        .starts_with(".preflight-write-")),
                "write probes must be removed"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn preflight_process_probe_has_a_hard_timeout_and_reclaims_the_tree() {
        let system_root = env::var_os("SystemRoot").expect("Windows test requires SystemRoot");
        let command = PathBuf::from(system_root).join("System32").join("cmd.exe");
        let started = Instant::now();
        let failure = run_bounded_probe_command(
            &command,
            &["/D", "/C", "ping -n 30 127.0.0.1 >NUL"],
            Duration::from_millis(150),
        )
        .expect_err("long-running probe should time out");

        assert_eq!(failure.kind, ProbeCommandFailureKind::Timeout);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[cfg(unix)]
    #[test]
    fn preflight_process_probe_has_a_hard_timeout_and_reclaims_the_tree() {
        let started = Instant::now();
        let failure = run_bounded_probe_command(
            Path::new("/bin/sh"),
            &["-c", "sleep 30 & wait"],
            Duration::from_millis(150),
        )
        .expect_err("long-running probe should time out");

        assert_eq!(failure.kind, ProbeCommandFailureKind::Timeout);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[cfg(windows)]
    #[test]
    fn backend_process_enters_its_job_before_executing() {
        let mut command = Command::new("cmd.exe");
        command
            .args(["/D", "/C", "exit", "/B", "0"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_backend_process_creation(&mut command);
        let mut child = command
            .spawn()
            .expect("suspended backend fixture should start");
        let job = match attach_kill_on_close_job(&child) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                panic!("suspended backend fixture should enter its job: {error}");
            }
        };

        thread::sleep(Duration::from_millis(100));
        assert!(
            child
                .try_wait()
                .expect("suspended fixture should remain inspectable")
                .is_none(),
            "the backend command executed before its process job was assigned"
        );
        resume_suspended_process(&child).expect("assigned backend fixture should resume");
        let status = wait_for_child_exit_until(&mut child, Instant::now() + Duration::from_secs(3))
            .expect("resumed fixture should remain inspectable")
            .expect("resumed fixture should exit");
        assert!(status.success());
        drop(job);
    }

    #[test]
    fn backend_log_reader_caps_unterminated_lines_without_unbounded_allocation() {
        let mut input = vec![b'a'; MAX_LOG_LINE_BYTES * 12];
        input.extend_from_slice(b"\r\nshort\n");
        let mut lines = Vec::new();

        read_bounded_log_stream(io::Cursor::new(input), |line| lines.push(line))
            .expect("bounded log stream should read");

        assert_eq!(lines.len(), 2);
        assert!(lines[0].ends_with(LOG_TRUNCATION_SUFFIX));
        assert!(lines[0].len() <= MAX_LOG_LINE_BYTES);
        assert_eq!(lines[1], "short");
    }

    #[test]
    fn health_probe_sends_nonce_and_accepts_authenticated_contract() {
        let session_secret = "ef".repeat(32);
        let response_secret = session_secret.clone();
        let (address, request_receiver) = serve_health_once(move |request| {
            let nonce =
                request_header(request, "X-MPP-Desktop-Nonce").expect("probe should send a nonce");
            health_response(200, compatible_health_body(&response_secret, &nonce))
        });

        assert_eq!(
            probe_backend_at(address, &session_secret),
            BackendProbe::Compatible
        );
        let request = String::from_utf8(
            request_receiver
                .recv_timeout(Duration::from_secs(2))
                .expect("probe request should be captured"),
        )
        .expect("request should be UTF-8");
        assert!(request.starts_with("GET /health HTTP/1.1\r\n"));
        let nonce = request_header(request.as_bytes(), "X-MPP-Desktop-Nonce")
            .expect("request should contain the challenge nonce");
        assert!(is_lower_hex(&nonce, 64));
        assert!(!request.contains(&session_secret));
    }

    #[test]
    fn transient_health_timeout_does_not_claim_the_managed_port_is_occupied() {
        let listener =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("slow health fixture should bind");
        let address = listener
            .local_addr()
            .expect("slow health fixture should have an address");
        thread::spawn(move || {
            let (mut stream, _) = listener
                .accept()
                .expect("slow health fixture should accept");
            let _ = read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(1));
            thread::sleep(Duration::from_millis(150));
        });

        assert_eq!(
            probe_backend_at_with_timeouts(
                address,
                &"ef".repeat(32),
                Duration::from_millis(250),
                Duration::from_millis(40),
            ),
            BackendProbe::Unavailable
        );
    }

    #[test]
    fn health_json_distinguishes_incompatible_and_occupied_services() {
        let session_secret = "ab".repeat(32);
        let nonce = "cd".repeat(32);
        let mut incompatible = compatible_health_body(&session_secret, &nonce);
        incompatible["protocol"] = serde_json::json!(HEALTH_PROTOCOL + 1);
        assert!(matches!(
            classify_health_response(&health_response(200, incompatible), &session_secret, &nonce,),
            BackendProbe::Incompatible(_)
        ));

        let unrelated = serde_json::json!({
            "status": "healthy",
            "product": "other.product",
            "protocol": HEALTH_PROTOCOL,
            "service": HEALTH_SERVICE,
            "version": env!("CARGO_PKG_VERSION"),
        });
        assert!(matches!(
            classify_health_response(&health_response(200, unrelated), &session_secret, &nonce,),
            BackendProbe::Occupied(_)
        ));

        let mut forged = compatible_health_body(&session_secret, &nonce);
        forged["desktopProof"] = serde_json::json!("00".repeat(32));
        assert!(matches!(
            classify_health_response(&health_response(200, forged), &session_secret, &nonce,),
            BackendProbe::Incompatible(_)
        ));
    }

    #[test]
    fn health_probe_rejects_an_occupied_non_mpp_port() {
        let response =
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n\r\nOK".to_vec();
        let (address, _request_receiver) = serve_health_once(move |_| response);

        assert!(matches!(
            probe_backend_at(address, &"12".repeat(32)),
            BackendProbe::Occupied(_)
        ));
    }

    #[test]
    fn localhost_probe_requires_both_address_families() {
        assert_eq!(
            combine_loopback_probes(BackendProbe::Compatible, BackendProbe::Compatible),
            BackendProbe::Compatible
        );
        assert_eq!(
            combine_loopback_probes(BackendProbe::Compatible, BackendProbe::Unavailable),
            BackendProbe::Unavailable
        );
        assert_eq!(
            combine_loopback_probes_with_policy(
                BackendProbe::Compatible,
                BackendProbe::Unavailable,
                false
            ),
            BackendProbe::Compatible
        );
        assert!(matches!(
            combine_loopback_probes(
                BackendProbe::Compatible,
                BackendProbe::Occupied("unexpected listener".to_string())
            ),
            BackendProbe::Occupied(reason) if reason.contains("IPv6 localhost")
        ));
    }

    #[test]
    fn health_hmac_matches_shared_golden_vector_and_allows_additive_fields() {
        let secret = "ab".repeat(32);
        let nonce = "cd".repeat(32);
        assert_eq!(
            desktop_health_proof(&secret, &nonce).expect("golden HMAC should compute"),
            "290970beb86e5dc6846601100b0fd0a5419b0e612582912686e9570605b24e25"
        );

        let mut body = compatible_health_body(&secret, &nonce);
        body["futureCapability"] = serde_json::json!({"schema": 2});
        assert_eq!(
            classify_health_response(&health_response(200, body), &secret, &nonce),
            BackendProbe::Compatible
        );
    }

    #[test]
    fn webview_proxy_cookie_unlocks_session_injection_without_leaking_cookie() {
        let layout = proxy_test_layout(43123);
        let request = format!(
            "POST /api/tasks HTTP/1.1\r\nHost: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n\
             Origin: http://tauri.localhost\r\nAuthorization: Bearer user-token\r\n\
             Cookie: theme=dark; {DESKTOP_PROXY_COOKIE}={}\r\n\
             X-MPP-Desktop-Session: attacker-value\r\nRange: bytes=10-20\r\n\
             Content-Length: 4\r\n\r\ndata",
            layout.proxy_token
        );
        let forwarded = forwarded_request(
            prepare_test_request(request.into_bytes(), &layout)
                .expect("trusted WebView request should be prepared"),
        );
        let head = String::from_utf8(forwarded.head).expect("rewritten headers should be UTF-8");

        assert!(head.starts_with("POST /api/tasks HTTP/1.1\r\n"));
        assert!(head.contains(&format!("Host: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n")));
        assert!(head.contains("Authorization: Bearer user-token\r\n"));
        assert!(head.contains("Range: bytes=10-20\r\n"));
        assert!(head.contains("Cookie: theme=dark\r\n"));
        assert!(head.contains(&format!(
            "X-MPP-Desktop-Session: {}\r\n",
            layout.session_token
        )));
        assert!(!head.contains(&layout.proxy_token));
        assert!(!head.contains("attacker-value"));
        assert_eq!(forwarded.body_prefix, b"data");
        assert_eq!(forwarded.body_framing, RequestBodyFraming::ContentLength(4));
    }

    #[test]
    fn webview_proxy_rejects_missing_cookie_and_serves_strict_preflight() {
        let layout = proxy_test_layout(43123);
        let missing_cookie = format!(
            "GET /api/tasks HTTP/1.1\r\nHost: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n\
             Origin: http://tauri.localhost\r\n\r\n"
        );
        let failure = prepare_test_request(missing_cookie.into_bytes(), &layout)
            .expect_err("trusted WebView host should require the private cookie");
        assert_eq!(failure.kind, ProxyFailureKind::Unauthorized);
        assert_eq!(
            failure.cors_origin.as_deref(),
            Some("http://tauri.localhost")
        );

        let preflight = format!(
            "OPTIONS /api/tasks HTTP/1.1\r\nHost: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n\
             Origin: http://tauri.localhost\r\n\
             Access-Control-Request-Method: POST\r\n\
             Access-Control-Request-Headers: Authorization, X-Requested-With\r\n\r\n"
        );
        let ProxyRequestAction::Preflight(response) =
            prepare_test_request(preflight.into_bytes(), &layout)
                .expect("strict WebView preflight should be answered locally")
        else {
            panic!("expected a local preflight response");
        };
        let response = String::from_utf8(response).expect("preflight should be UTF-8");
        assert!(response.starts_with("HTTP/1.1 204 No Content\r\n"));
        assert!(response.contains("Access-Control-Allow-Origin: http://tauri.localhost\r\n"));
        assert!(response.contains("Access-Control-Allow-Credentials: true\r\n"));
        assert!(
            response.contains("Access-Control-Allow-Headers: authorization, x-requested-with\r\n")
        );
    }

    #[test]
    fn public_cli_and_same_origin_browser_use_normal_backend_auth() {
        let layout = proxy_test_layout(43123);
        let cli_health = forwarded_request(
            prepare_test_request(
                b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:18000\r\n\r\n".to_vec(),
                &layout,
            )
            .expect("public CLI health request should pass"),
        );
        let cli_health_head =
            String::from_utf8(cli_health.head).expect("CLI health headers should be UTF-8");
        assert!(!cli_health_head.contains("X-MPP-Desktop-Session"));

        let cli_api = forwarded_request(
            prepare_test_request(
                b"POST /api/settings HTTP/1.1\r\nHost: localhost:18000\r\n\
                  Authorization: Bearer configured-token\r\nX-Requested-With: fetch\r\n\
                  X-MPP-Desktop-Session: spoofed\r\nContent-Length: 2\r\n\r\n{}"
                    .to_vec(),
                &layout,
            )
            .expect("authenticated public CLI request should pass to backend auth"),
        );
        let cli_api_head =
            String::from_utf8(cli_api.head).expect("CLI API headers should be UTF-8");
        assert!(cli_api_head.contains("Authorization: Bearer configured-token\r\n"));
        assert!(cli_api_head.contains("X-Requested-With: fetch\r\n"));
        assert!(!cli_api_head.contains("X-MPP-Desktop-Session"));
        assert!(!cli_api_head.contains("spoofed"));

        let browser = b"GET / HTTP/1.1\r\nHost: localhost:18000\r\n\
                        Origin: http://localhost:18000\r\nSec-Fetch-Site: same-origin\r\n\r\n"
            .to_vec();
        assert!(prepare_test_request(browser, &layout).is_ok());
        let ipv6_browser = b"GET / HTTP/1.1\r\nHost: [::1]:18000\r\n\
                             Origin: http://[::1]:18000\r\n\
                             Sec-Fetch-Site: same-origin\r\n\r\n"
            .to_vec();
        assert!(prepare_test_request(ipv6_browser, &layout).is_ok());
    }

    #[test]
    fn public_proxy_rejects_cross_site_browser_and_ambiguous_framing() {
        let layout = proxy_test_layout(43123);
        for request in [
            b"GET /api/tasks HTTP/1.1\r\nHost: localhost:18000\r\n\
              Origin: https://attacker.invalid\r\n\r\n"
                .to_vec(),
            b"GET /api/tasks HTTP/1.1\r\nHost: localhost:18000\r\n\
              Sec-Fetch-Site: cross-site\r\n\r\n"
                .to_vec(),
            b"POST /api/tasks HTTP/1.1\r\nHost: localhost:18000\r\n\
              Content-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n"
                .to_vec(),
        ] {
            assert!(
                prepare_test_request(request, &layout).is_err(),
                "unsafe public proxy request should be rejected"
            );
        }
    }

    #[test]
    fn ipv6_listener_fallback_only_accepts_unavailable_errors() {
        assert!(ipv6_listener_is_unavailable(&io::Error::new(
            io::ErrorKind::AddrNotAvailable,
            "IPv6 disabled"
        )));
        assert!(ipv6_listener_is_unavailable(&io::Error::new(
            io::ErrorKind::Unsupported,
            "IPv6 unsupported"
        )));
        assert!(!ipv6_listener_is_unavailable(&io::Error::new(
            io::ErrorKind::AddrInUse,
            "IPv6 address already owned"
        )));
        #[cfg(windows)]
        {
            assert!(ipv6_listener_is_unavailable(&io::Error::from_raw_os_error(
                10043
            )));
            assert!(ipv6_listener_is_unavailable(&io::Error::from_raw_os_error(
                10047
            )));
            assert!(ipv6_listener_is_unavailable(&io::Error::from_raw_os_error(
                10049
            )));
            assert!(!ipv6_listener_is_unavailable(
                &io::Error::from_raw_os_error(10048)
            ));
        }
    }

    #[test]
    fn absolute_header_deadline_stops_slowloris_connections() {
        let listener =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("slowloris fixture should bind");
        let address = listener
            .local_addr()
            .expect("slowloris fixture should have an address");
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("slowloris fixture should accept");
            let started = Instant::now();
            let result = read_http_head_until(&mut stream, started + Duration::from_millis(180));
            sender
                .send((started.elapsed(), result))
                .expect("slowloris result should be captured");
        });
        let mut client = TcpStream::connect(address).expect("slowloris client should connect");
        for byte in b"GET / HTTP/1.1\r\nHost: localhost:18000\r\n" {
            if client.write_all(&[*byte]).is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(35));
        }
        let (elapsed, result) = receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("absolute deadline should finish promptly");
        assert!(result.is_err());
        assert!(
            elapsed < Duration::from_secs(1),
            "absolute deadline took {elapsed:?}"
        );
    }

    #[test]
    fn upload_body_pumps_content_length_and_chunked_framing() {
        let (unused_client, source_stream) = tcp_pair();
        let (mut body_sink, mut body_reader) = tcp_pair();
        let mut reader = PrefixReader::new(b"upload-data".to_vec(), source_stream);
        copy_content_length_body(&mut reader, &mut body_sink, 11)
            .expect("Content-Length upload should pump");
        body_sink
            .shutdown(Shutdown::Write)
            .expect("body sink should close");
        let mut received = Vec::new();
        body_reader
            .read_to_end(&mut received)
            .expect("pumped upload should read");
        assert_eq!(received, b"upload-data");
        drop(unused_client);

        let (unused_client, source_stream) = tcp_pair();
        let (mut body_sink, mut body_reader) = tcp_pair();
        let chunked = b"4\r\nWiki\r\n5\r\npedia\r\n0\r\nChecksum: ok\r\n\r\n";
        let mut reader = PrefixReader::new(chunked.to_vec(), source_stream);
        copy_chunked_body(&mut reader, &mut body_sink).expect("chunked upload should pump");
        body_sink
            .shutdown(Shutdown::Write)
            .expect("chunked sink should close");
        let mut received = Vec::new();
        body_reader
            .read_to_end(&mut received)
            .expect("chunked upload should read");
        assert_eq!(received, chunked);
        drop(unused_client);
    }

    #[test]
    fn same_backend_connection_is_authenticated_before_range_request_forwarding() {
        let backend =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("authenticated backend should bind");
        let backend_port = backend
            .local_addr()
            .expect("authenticated backend should have an address")
            .port();
        let layout = proxy_test_layout(backend_port);
        let response_secret = layout.session_token.clone();
        let (forwarded_sender, forwarded_receiver) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = backend.accept().expect("backend should accept once");
            let (challenge, _) =
                read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                    .expect("health challenge should arrive");
            let nonce = request_header(&challenge, "X-MPP-Desktop-Nonce")
                .expect("health challenge should contain a nonce");
            stream
                .write_all(&health_response_with_connection(
                    200,
                    compatible_health_body(&response_secret, &nonce),
                    "keep-alive",
                ))
                .expect("authenticated health response should write");

            let (request, _) =
                read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                    .expect("forwarded request should arrive on the same connection");
            forwarded_sender
                .send((challenge, request))
                .expect("forwarded request should be captured");
            stream
                .write_all(
                    b"HTTP/1.1 206 Partial Content\r\nContent-Length: 5\r\n\
                      Content-Range: bytes 10-14/20\r\nConnection: close\r\n\r\nRANGE",
                )
                .expect("range response should write");
        });

        let (mut client, result_receiver) = serve_proxy_once(layout.clone());
        client
            .write_all(
                format!(
                    "GET /api/files/demo/content HTTP/1.1\r\n\
                     Host: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n\
                     Origin: http://tauri.localhost\r\nRange: bytes=10-14\r\n\
                     Authorization: Bearer user-token\r\n\
                     Cookie: {DESKTOP_PROXY_COOKIE}={}\r\n\r\n",
                    layout.proxy_token
                )
                .as_bytes(),
            )
            .expect("range request should write");
        let mut response = Vec::new();
        client
            .read_to_end(&mut response)
            .expect("range response should relay");
        assert!(String::from_utf8_lossy(&response).starts_with("HTTP/1.1 206 Partial Content"));
        assert!(response.ends_with(b"RANGE"));
        result_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("proxy result should arrive")
            .expect("range relay should succeed");

        let (challenge, forwarded) = forwarded_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("backend requests should be captured");
        let challenge = String::from_utf8(challenge).expect("challenge should be UTF-8");
        let forwarded = String::from_utf8(forwarded).expect("forwarded request should be UTF-8");
        assert!(!challenge.contains("Authorization"));
        assert!(!challenge.contains(&layout.session_token));
        assert!(forwarded.contains("Range: bytes=10-14\r\n"));
        assert!(forwarded.contains("Authorization: Bearer user-token\r\n"));
        assert!(forwarded.contains(&format!(
            "X-MPP-Desktop-Session: {}\r\n",
            layout.session_token
        )));
        assert!(!forwarded.contains(&layout.proxy_token));
    }

    #[test]
    fn invalid_backend_hmac_never_receives_client_authorization() {
        let backend =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("forged backend should bind");
        let backend_port = backend
            .local_addr()
            .expect("forged backend should have an address")
            .port();
        let layout = proxy_test_layout(backend_port);
        let (captured_sender, captured_receiver) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = backend.accept().expect("forged backend should accept");
            let (challenge, _) =
                read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                    .expect("health challenge should arrive");
            let nonce = request_header(&challenge, "X-MPP-Desktop-Nonce")
                .expect("health challenge should contain a nonce");
            let mut body = compatible_health_body(&"ef".repeat(32), &nonce);
            body["desktopProof"] = serde_json::json!("00".repeat(32));
            stream
                .write_all(&health_response_with_connection(200, body, "keep-alive"))
                .expect("forged health response should write");
            stream
                .set_read_timeout(Some(Duration::from_millis(500)))
                .expect("capture timeout should apply");
            let mut later = Vec::new();
            let _ = stream.read_to_end(&mut later);
            captured_sender
                .send((challenge, later))
                .expect("forged backend capture should send");
        });

        let (mut client, result_receiver) = serve_proxy_once(layout.clone());
        client
            .write_all(
                format!(
                    "GET /api/tasks HTTP/1.1\r\nHost: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n\
                     Origin: http://tauri.localhost\r\nAuthorization: Bearer never-leak\r\n\
                     Cookie: {DESKTOP_PROXY_COOKIE}={}\r\n\r\n",
                    layout.proxy_token
                )
                .as_bytes(),
            )
            .expect("client request should write");
        let failure = result_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("proxy failure should arrive")
            .expect_err("forged backend must fail authentication");
        assert_eq!(failure.kind, ProxyFailureKind::BadGateway);
        assert_eq!(
            failure.cors_origin.as_deref(),
            Some("http://tauri.localhost")
        );
        let (challenge, later) = captured_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("forged backend traffic should be captured");
        assert!(!String::from_utf8_lossy(&challenge).contains("never-leak"));
        assert!(
            later.is_empty(),
            "client bytes reached forged backend: {}",
            String::from_utf8_lossy(&later)
        );
    }

    #[test]
    fn authenticated_proxy_streams_sse_until_backend_closes() {
        let backend = TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("SSE backend should bind");
        let backend_port = backend
            .local_addr()
            .expect("SSE backend should have an address")
            .port();
        let layout = proxy_test_layout(backend_port);
        let response_secret = layout.session_token.clone();
        thread::spawn(move || {
            let (mut stream, _) = backend.accept().expect("SSE backend should accept");
            let (challenge, _) =
                read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                    .expect("SSE health challenge should arrive");
            let nonce = request_header(&challenge, "X-MPP-Desktop-Nonce")
                .expect("SSE health challenge should contain nonce");
            stream
                .write_all(&health_response_with_connection(
                    200,
                    compatible_health_body(&response_secret, &nonce),
                    "keep-alive",
                ))
                .expect("SSE health response should write");
            let _ = read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                .expect("SSE request should arrive");
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\
                      Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n",
                )
                .expect("SSE headers should write");
            stream
                .write_all(b"D\r\ndata: one\r\n\r\n")
                .expect("first SSE event should write");
            thread::sleep(Duration::from_millis(120));
            stream
                .write_all(b"D\r\ndata: two\r\n\r\n0\r\n\r\n")
                .expect("second SSE event should write");
        });

        let (mut client, result_receiver) = serve_proxy_once(layout.clone());
        client
            .write_all(
                format!(
                    "GET /api/tasks/events HTTP/1.1\r\n\
                     Host: {DESKTOP_API_HOST}:{BACKEND_PORT}\r\n\
                     Origin: http://tauri.localhost\r\nAccept: text/event-stream\r\n\
                     Cookie: {DESKTOP_PROXY_COOKIE}={}\r\n\r\n",
                    layout.proxy_token
                )
                .as_bytes(),
            )
            .expect("SSE request should write");
        let mut response = Vec::new();
        client
            .read_to_end(&mut response)
            .expect("SSE stream should relay");
        let response = String::from_utf8_lossy(&response);
        assert!(response.contains("data: one"));
        assert!(response.contains("data: two"));
        result_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("SSE proxy result should arrive")
            .expect("SSE relay should succeed");
    }

    #[test]
    fn graceful_shutdown_uses_authenticated_private_connection() {
        let backend =
            TcpListener::bind((BACKEND_IPV4_HOST, 0)).expect("shutdown backend should bind");
        let backend_port = backend
            .local_addr()
            .expect("shutdown backend should have an address")
            .port();
        let layout = proxy_test_layout(backend_port);
        let response_secret = layout.session_token.clone();
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = backend.accept().expect("shutdown backend should accept");
            let (challenge, _) =
                read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                    .expect("shutdown health challenge should arrive");
            let nonce = request_header(&challenge, "X-MPP-Desktop-Nonce")
                .expect("shutdown health challenge should contain nonce");
            stream
                .write_all(&health_response_with_connection(
                    200,
                    compatible_health_body(&response_secret, &nonce),
                    "keep-alive",
                ))
                .expect("shutdown health response should write");
            let (shutdown, _) =
                read_http_head_until(&mut stream, Instant::now() + Duration::from_secs(2))
                    .expect("shutdown request should arrive");
            sender
                .send(shutdown)
                .expect("shutdown request should be captured");
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
                      Content-Length: 26\r\nConnection: close\r\n\r\n\
                      {\"status\":\"shutting_down\"}",
                )
                .expect("shutdown response should write");
        });

        request_private_backend_shutdown(&layout)
            .expect("authenticated graceful shutdown should succeed");
        let request = String::from_utf8(
            receiver
                .recv_timeout(Duration::from_secs(2))
                .expect("shutdown request should be captured"),
        )
        .expect("shutdown request should be UTF-8");
        assert!(request.starts_with("POST /api/desktop/shutdown HTTP/1.1\r\n"));
        assert!(request.contains(&format!(
            "X-MPP-Desktop-Session: {}\r\n",
            layout.session_token
        )));
        assert!(request.contains("X-Requested-With: fetch\r\n"));
    }

    #[test]
    fn external_url_validation_is_strict_and_shell_independent() {
        assert_eq!(
            validated_external_url(" https://example.invalid/watch?v=1&next=2 ")
                .expect("ordinary HTTPS URL should be accepted"),
            "https://example.invalid/watch?v=1&next=2"
        );
        assert!(validated_external_url("file:///C:/Windows/System32/calc.exe").is_err());
        assert!(validated_external_url("https://user:secret@example.invalid").is_err());
        assert!(validated_external_url("http://example.invalid\\@attacker.invalid").is_err());
        assert!(validated_external_url("https://example.invalid/\nnext").is_err());
        for metacharacters in ["&", "|", "<", ">", "^", "%21", "!", "(", ")"] {
            let candidate = format!("https://example.invalid/path?value={metacharacters}");
            assert!(
                validated_external_url(&candidate).is_ok(),
                "URL metacharacters are passed directly to ShellExecuteW: {candidate}"
            );
        }
    }

    #[test]
    fn bootstrap_failure_phases_and_status_fields_are_stable() {
        let retryable = BootstrapFailure::retryable(
            "PORT_IN_USE",
            "desktop-proxy",
            "Close the conflicting process and retry.",
            "localhost port 18000 is occupied",
            Some(Path::new(
                "C:/Users/test/AppData/Local/MediaProcessPipeline",
            )),
        );
        let manual = BootstrapFailure::manual(
            "RUNTIME_INVALID",
            "desktop-runtime",
            "Repair the installation.",
            "runtime manifest is invalid",
            None,
        );
        assert_eq!(retryable.phase(), "FAILED_RETRYABLE");
        assert_eq!(manual.phase(), "FAILED_MANUAL");
        assert_eq!(retryable.error_code, "PORT_IN_USE");
        assert_eq!(manual.component_id, "desktop-runtime");

        let status = BackendStatus {
            state: "error".to_string(),
            command: BACKEND_COMMAND.to_string(),
            cwd: "backend".to_string(),
            pid: None,
            url: APP_URL.to_string(),
            message: retryable.detail.clone(),
            phase: retryable.phase().to_string(),
            error_code: Some(retryable.error_code.to_string()),
            component_id: Some(retryable.component_id.to_string()),
            remediation: Some(retryable.remediation.to_string()),
            local_path: retryable.local_path.clone(),
        };
        let serialized = serde_json::to_value(status).expect("status should serialize");
        assert_eq!(serialized["phase"], "FAILED_RETRYABLE");
        assert_eq!(serialized["error_code"], "PORT_IN_USE");
        assert_eq!(serialized["component_id"], "desktop-proxy");
        assert!(serialized["remediation"].as_str().is_some());
        assert!(serialized["local_path"].as_str().is_some());
    }

    #[test]
    fn bootstrap_attempt_epoch_rejects_stale_completion_and_shutdown_work() {
        let mut runtime = BootstrapRuntime::default();
        assert!(bootstrap_controls_status(&runtime));

        let first = begin_bootstrap_attempt(&mut runtime).expect("first attempt should start");
        assert!(bootstrap_attempt_is_current(&runtime, first));
        assert!(begin_bootstrap_attempt(&mut runtime).is_none());
        end_bootstrap_attempt(&mut runtime, first);

        let second = begin_bootstrap_attempt(&mut runtime).expect("retry should advance epoch");
        assert_ne!(first, second);
        assert!(!bootstrap_attempt_is_current(&runtime, first));
        end_bootstrap_attempt(&mut runtime, first);
        assert!(bootstrap_attempt_is_current(&runtime, second));

        cancel_bootstrap_runtime_for_shutdown(&mut runtime);
        assert!(!bootstrap_attempt_is_current(&runtime, second));
        assert!(runtime.shutdown_requested);
        assert!(!runtime.bootstrap_complete);
        assert!(begin_bootstrap_attempt(&mut runtime).is_none());
        assert!(bootstrap_controls_status(&runtime));
    }

    #[test]
    fn completed_bootstrap_releases_status_ownership_only_after_attempt_finishes() {
        let mut runtime = BootstrapRuntime::default();
        let attempt = begin_bootstrap_attempt(&mut runtime).expect("attempt should start");
        runtime.bootstrap_complete = true;
        assert!(bootstrap_controls_status(&runtime));

        end_bootstrap_attempt(&mut runtime, attempt);
        assert!(!bootstrap_controls_status(&runtime));
    }

    #[test]
    fn bootstrap_diagnostics_are_flushed_and_replace_only_regular_files() {
        let temporary = TestDirectory::new("bootstrap-diagnostics");
        let log_dir = temporary.path.join("logs");
        write_bootstrap_diagnostics(&log_dir, b"{\"attempt\":1}\n")
            .expect("first diagnostics snapshot should be written");
        write_bootstrap_diagnostics(&log_dir, b"{\"attempt\":2}\n")
            .expect("regular diagnostics snapshot should be replaced");

        assert_eq!(
            fs::read(log_dir.join("desktop-bootstrap.json"))
                .expect("diagnostics snapshot should be readable"),
            b"{\"attempt\":2}\n"
        );
        assert!(
            fs::read_dir(&log_dir)
                .expect("log directory should be readable")
                .all(|entry| {
                    !entry
                        .expect("log directory entry should be readable")
                        .file_name()
                        .to_string_lossy()
                        .starts_with(".desktop-bootstrap-")
                }),
            "temporary diagnostics files must be cleaned up"
        );
    }

    #[cfg(windows)]
    #[test]
    fn bootstrap_diagnostics_reject_a_windows_reparse_target() {
        let temporary = TestDirectory::new("bootstrap-diagnostics-junction");
        let log_dir = temporary.path.join("logs");
        let redirect_target = temporary.path.join("redirect-target");
        let diagnostics = log_dir.join("desktop-bootstrap.json");
        fs::create_dir_all(&log_dir).expect("log directory should be created");
        fs::create_dir_all(&redirect_target).expect("redirect target should be created");
        let output = Command::new("cmd")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&diagnostics)
            .arg(&redirect_target)
            .output()
            .expect("cmd should create a diagnostics junction");
        assert!(
            output.status.success(),
            "junction fixture failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );

        let error = write_bootstrap_diagnostics(&log_dir, b"blocked")
            .expect_err("diagnostics must reject a reparse target");
        assert!(error.contains("reparse point") || error.contains("symlink"));
        fs::remove_dir(&diagnostics).expect("diagnostics junction should be removed");
    }

    #[cfg(unix)]
    #[test]
    fn bootstrap_diagnostics_reject_a_symlink_target() {
        use std::os::unix::fs::symlink;

        let temporary = TestDirectory::new("bootstrap-diagnostics-symlink");
        let log_dir = temporary.path.join("logs");
        let redirect_target = temporary.path.join("redirect-target.json");
        let diagnostics = log_dir.join("desktop-bootstrap.json");
        fs::create_dir_all(&log_dir).expect("log directory should be created");
        write_file(&redirect_target, "preserve");
        symlink(&redirect_target, &diagnostics).expect("diagnostics symlink should be created");

        let error = write_bootstrap_diagnostics(&log_dir, b"blocked")
            .expect_err("diagnostics must reject a symlink target");
        assert!(error.contains("symlink") || error.contains("reparse point"));
        assert_eq!(
            fs::read_to_string(&redirect_target).expect("redirect target should remain readable"),
            "preserve"
        );
    }

    #[test]
    fn backend_wait_failures_map_to_actionable_bootstrap_codes() {
        let layout = proxy_test_layout(18001);
        let timeout = bootstrap_wait_failure(BackendWaitFailure::Timeout, &layout);
        let exited =
            bootstrap_wait_failure(BackendWaitFailure::ProcessExited("backend exited"), &layout);
        let incompatible = bootstrap_wait_failure(
            BackendWaitFailure::Incompatible("protocol mismatch".to_string()),
            &layout,
        );

        assert_eq!(timeout.error_code, "BACKEND_START_TIMEOUT");
        assert!(timeout.retryable);
        assert_eq!(exited.error_code, "BACKEND_EXITED");
        assert!(exited.retryable);
        assert_eq!(incompatible.error_code, "BACKEND_HEALTH_INVALID");
        assert!(!incompatible.retryable);
    }

    #[test]
    fn navigation_guard_only_accepts_the_packaged_app_origin() {
        for accepted in [
            "about:blank",
            "tauri://localhost/index.html",
            "tauri://localhost/tasks/abc?tab=mindmap",
            "http://tauri.localhost/index.html",
            "http://tauri.localhost/files/demo?preview=1",
        ] {
            assert!(
                trusted_app_navigation(&url::Url::parse(accepted).expect("URL should parse")),
                "packaged app URL should be trusted: {accepted}"
            );
        }
        for rejected in [
            "https://example.invalid/",
            "https://tauri.localhost/",
            "http://tauri.localhost:18000/",
            "http://api.tauri.localhost:18000/",
            "http://tauri.localhost:4444/",
            "http://user@tauri.localhost/",
            "http://localhost.evil.invalid:18000/",
            "file:///C:/temp/index.html",
            "data:text/html,attack",
        ] {
            assert!(
                !trusted_app_navigation(&url::Url::parse(rejected).expect("URL should parse")),
                "external URL should be rejected: {rejected}"
            );
        }

        let app_url = packaged_app_navigation_url().expect("packaged app URL should build");
        assert!(trusted_app_navigation(&app_url));
        let query = app_url.query_pairs().collect::<BTreeMap<_, _>>();
        assert_eq!(
            query.get("appVersion").map(|value| value.as_ref()),
            Some(env!("CARGO_PKG_VERSION"))
        );
        assert_eq!(
            query.get("source").map(|value| value.as_ref()),
            Some(env!("MPP_BUILD_COMMIT"))
        );
        assert_eq!(
            query.get("runtime").map(|value| value.as_ref()),
            Some(env!("MPP_RUNTIME_MANIFEST_SHA256"))
        );
        assert!(!query.contains_key("bootstrap"));

        let bootstrap =
            packaged_bootstrap_navigation_url().expect("packaged bootstrap URL should build");
        assert!(trusted_app_navigation(&bootstrap));
        let bootstrap_query = bootstrap.query_pairs().collect::<BTreeMap<_, _>>();
        assert_eq!(
            bootstrap_query.get("bootstrap").map(|value| value.as_ref()),
            Some("1")
        );
        assert_eq!(
            bootstrap_query
                .get("appVersion")
                .map(|value| value.as_ref()),
            Some(env!("CARGO_PKG_VERSION"))
        );
    }

    #[test]
    fn generated_session_tokens_are_strongly_sized_and_distinct() {
        let first = generate_session_token().expect("first token should generate");
        let second = generate_session_token().expect("second token should generate");

        assert!(is_lower_hex(&first, 64));
        assert!(is_lower_hex(&second, 64));
        assert_ne!(first, second);
    }
}
