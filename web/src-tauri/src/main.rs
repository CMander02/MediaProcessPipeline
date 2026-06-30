#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    env,
    error::Error,
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use chrono::Utc;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 18000;
const APP_URL: &str = "http://127.0.0.1:18000";
const BACKEND_COMMAND: &str = "uv run python -u -m app.cli serve";
const MAX_LOG_LINES: usize = 1200;
const HEALTH_REQUEST: &[u8] =
    b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:18000\r\nConnection: close\r\n\r\n";

#[derive(Clone, Serialize)]
struct BackendStatus {
    state: String,
    command: String,
    cwd: String,
    pid: Option<u32>,
    url: String,
    message: String,
}

#[derive(Clone, Serialize)]
struct BackendLogEntry {
    ts: String,
    source: String,
    line: String,
}

struct BackendProcess {
    child: Mutex<Option<Child>>,
    owns_backend: Mutex<bool>,
    status: Mutex<BackendStatus>,
    logs: Mutex<Vec<BackendLogEntry>>,
}

impl Default for BackendProcess {
    fn default() -> Self {
        Self {
            child: Mutex::new(None),
            owns_backend: Mutex::new(false),
            status: Mutex::new(BackendStatus {
                state: "stopped".to_string(),
                command: BACKEND_COMMAND.to_string(),
                cwd: "backend".to_string(),
                pid: None,
                url: APP_URL.to_string(),
                message: "Not started.".to_string(),
            }),
            logs: Mutex::new(Vec::new()),
        }
    }
}

fn boxed_error(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(std::io::Error::new(
        std::io::ErrorKind::Other,
        message.into(),
    ))
}

fn is_project_root(path: &Path) -> bool {
    path.join("pyproject.toml").is_file() && path.join("backend").join("app").is_dir()
}

fn resolve_project_root() -> Option<PathBuf> {
    if let Ok(value) = env::var("MPP_PROJECT_ROOT") {
        let candidate = PathBuf::from(value);
        if is_project_root(&candidate) {
            return Some(candidate);
        }
    }

    if let Ok(exe) = env::current_exe() {
        for ancestor in exe.ancestors() {
            if is_project_root(ancestor) {
                return Some(ancestor.to_path_buf());
            }
        }
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let candidate = manifest_dir.parent()?.parent()?.to_path_buf();
    is_project_root(&candidate).then_some(candidate)
}

fn uv_command() -> String {
    env::var("MPP_UV").unwrap_or_else(|_| "uv".to_string())
}

fn backend_cwd(project_root: &Path) -> String {
    project_root
        .join("backend")
        .to_string_lossy()
        .into_owned()
}

fn append_log(app: &AppHandle, source: &str, text: impl AsRef<str>) {
    let backend = app.state::<BackendProcess>();
    for raw_line in text.as_ref().replace("\r\n", "\n").replace('\r', "\n").split('\n') {
        let line = raw_line.trim_end();
        if line.is_empty() {
            continue;
        }

        let entry = BackendLogEntry {
            ts: Utc::now().to_rfc3339(),
            source: source.to_string(),
            line: line.to_string(),
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

    let next = status.clone();
    let _ = app.emit("mpp-backend:status", &next);
    Ok(next)
}

fn current_status(app: &AppHandle) -> Result<BackendStatus, String> {
    let backend = app.state::<BackendProcess>();
    backend
        .status
        .lock()
        .map(|status| status.clone())
        .map_err(|_| "backend status lock poisoned".to_string())
}

fn is_backend_healthy() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(350)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));

    if stream.write_all(HEALTH_REQUEST).is_err() {
        return false;
    }

    let mut buffer = [0_u8; 256];
    let read = match stream.read(&mut buffer) {
        Ok(read) => read,
        Err(_) => return false,
    };

    let response = String::from_utf8_lossy(&buffer[..read]);
    response.contains(" 200 ")
}

fn spawn_output_reader<R>(app: AppHandle, source: &'static str, pipe: R)
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut reader = BufReader::new(pipe);
        let mut buffer = Vec::new();

        loop {
            buffer.clear();
            match reader.read_until(b'\n', &mut buffer) {
                Ok(0) => break,
                Ok(_) => append_log(&app, source, String::from_utf8_lossy(&buffer)),
                Err(err) => {
                    append_log(&app, "error", format!("{source} read failed: {err}"));
                    break;
                }
            }
        }
    });
}

fn spawn_backend(app: &AppHandle, project_root: &Path) -> Result<Child, String> {
    let backend_dir = project_root.join("backend");
    append_log(app, "system", format!("Starting backend: {BACKEND_COMMAND}"));
    append_log(
        app,
        "system",
        format!("Working directory: {}", backend_dir.to_string_lossy()),
    );

    let mut command = Command::new(uv_command());
    command
        .current_dir(&backend_dir)
        .arg("run")
        .arg("--project")
        .arg(project_root)
        .arg("python")
        .arg("-u")
        .arg("-m")
        .arg("app.cli")
        .arg("serve")
        .arg("--host")
        .arg(BACKEND_HOST)
        .arg("--port")
        .arg(BACKEND_PORT.to_string())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .env("NO_COLOR", "1")
        .env("MPP_SKIP_VERSION_CHECK", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = command
        .spawn()
        .map_err(|err| format!("failed to start backend with uv: {err}"))?;

    if let Some(stdout) = child.stdout.take() {
        spawn_output_reader(app.clone(), "stdout", stdout);
    }
    if let Some(stderr) = child.stderr.take() {
        spawn_output_reader(app.clone(), "stderr", stderr);
    }

    Ok(child)
}

fn refresh_child_exit(app: &AppHandle) -> Result<Option<String>, String> {
    let backend = app.state::<BackendProcess>();
    let exit_status = {
        let mut child_guard = backend
            .child
            .lock()
            .map_err(|_| "backend child lock poisoned".to_string())?;

        match child_guard.as_mut() {
            Some(child) => match child
                .try_wait()
                .map_err(|err| format!("failed to inspect backend process: {err}"))?
            {
                Some(status) => {
                    *child_guard = None;
                    Some(status)
                }
                None => None,
            },
            None => None,
        }
    };

    if let Some(status) = exit_status {
        if let Ok(mut owns_backend) = backend.owns_backend.lock() {
            *owns_backend = false;
        }

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
    } else {
        Ok(None)
    }
}

fn wait_for_backend_ready(app: &AppHandle, timeout: Duration) -> Result<(), String> {
    let start = Instant::now();

    loop {
        if is_backend_healthy() {
            append_log(app, "system", "Backend health check passed.");
            let pid = {
                let backend = app.state::<BackendProcess>();
                backend
                    .child
                    .lock()
                    .ok()
                    .and_then(|guard| guard.as_ref().map(|child| child.id()))
            };
            let _ = set_status(app, "running", pid, "Backend is ready.", None)?;
            return Ok(());
        }

        if refresh_child_exit(app)?.is_some() {
            return Err("backend exited before it became healthy".to_string());
        }

        if start.elapsed() > timeout {
            append_log(app, "system", "Backend health check timed out.");
            return Err("backend did not become healthy within the wait window".to_string());
        }

        thread::sleep(Duration::from_millis(500));
    }
}

fn start_backend(app: &AppHandle) -> Result<BackendStatus, String> {
    refresh_child_exit(app)?;

    {
        let backend = app.state::<BackendProcess>();
        let child_guard = backend
            .child
            .lock()
            .map_err(|_| "backend child lock poisoned".to_string())?;
        if let Some(child) = child_guard.as_ref() {
            return set_status(
                app,
                "running",
                Some(child.id()),
                "Backend process is already managed by the desktop app.",
                None,
            );
        }
    }

    let project_root = resolve_project_root()
        .ok_or_else(|| "could not resolve MediaProcessPipeline project root".to_string())?;
    let cwd = backend_cwd(&project_root);

    if is_backend_healthy() {
        append_log(
            app,
            "system",
            "Detected an existing backend on 127.0.0.1:18000; desktop app will reuse it.",
        );
        let backend = app.state::<BackendProcess>();
        if let Ok(mut owns_backend) = backend.owns_backend.lock() {
            *owns_backend = false;
        }
        return set_status(
            app,
            "external",
            None,
            "Detected an existing backend on port 18000.",
            Some(cwd),
        );
    }

    let _ = set_status(app, "starting", None, "Starting backend...", Some(cwd))?;
    let child = spawn_backend(app, &project_root)?;
    let pid = child.id();

    {
        let backend = app.state::<BackendProcess>();
        *backend
            .owns_backend
            .lock()
            .map_err(|_| "backend ownership lock poisoned".to_string())? = true;
        *backend
            .child
            .lock()
            .map_err(|_| "backend child lock poisoned".to_string())? = Some(child);
    }

    set_status(
        app,
        "starting",
        Some(pid),
        "Backend process created; waiting for health check.",
        None,
    )
}

#[cfg(windows)]
fn terminate_child_tree(child: &mut Child) {
    let pid = child.id().to_string();
    let _ = Command::new("taskkill")
        .arg("/pid")
        .arg(pid)
        .arg("/t")
        .arg("/f")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(not(windows))]
fn terminate_child_tree(child: &mut Child) {
    let _ = child.kill();
}

fn stop_backend(app: &AppHandle) -> Result<BackendStatus, String> {
    let backend = app.state::<BackendProcess>();
    let owns_backend = backend
        .owns_backend
        .lock()
        .map(|guard| *guard)
        .unwrap_or(false);

    if !owns_backend {
        let status = current_status(app)?;
        if status.state == "external" {
            append_log(app, "system", "External backend detected; stop request left it running.");
            return set_status(
                app,
                "external",
                None,
                "Current backend is external and was left running.",
                None,
            );
        }
    }

    let mut child = {
        let mut child_guard = backend
            .child
            .lock()
            .map_err(|_| "backend child lock poisoned".to_string())?;
        child_guard.take()
    };

    if let Some(child) = child.as_mut() {
        let pid = child.id();
        append_log(app, "system", format!("Stopping backend process {pid}."));
        let _ = set_status(app, "stopping", Some(pid), "Stopping backend...", None)?;
        terminate_child_tree(child);
        let _ = child.wait();
    } else {
        append_log(app, "system", "Stop requested while backend was not managed.");
    }

    if let Ok(mut owns_backend) = backend.owns_backend.lock() {
        *owns_backend = false;
    }

    set_status(app, "stopped", None, "Backend stopped.", None)
}

fn restart_backend(app: &AppHandle) -> Result<BackendStatus, String> {
    let _ = stop_backend(app)?;
    thread::sleep(Duration::from_millis(500));
    start_backend(app)
}

fn stop_backend_on_close(state: &BackendProcess) {
    let owns_backend = state
        .owns_backend
        .lock()
        .map(|guard| *guard)
        .unwrap_or(false);

    if !owns_backend {
        return;
    }

    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            terminate_child_tree(&mut child);
            let _ = child.wait();
        }
    }
}

#[tauri::command]
fn backend_get_status(app: AppHandle) -> Result<BackendStatus, String> {
    refresh_child_exit(&app)?;

    let status = current_status(&app)?;
    if status.state == "stopped" && is_backend_healthy() {
        return set_status(
            &app,
            "external",
            None,
            "Detected an existing backend on port 18000.",
            None,
        );
    }

    current_status(&app)
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

#[tauri::command]
fn backend_start(app: AppHandle) -> Result<BackendStatus, String> {
    let status = start_backend(&app)?;
    if status.state == "starting" {
        let app_for_wait = app.clone();
        thread::spawn(move || {
            let _ = wait_for_backend_ready(&app_for_wait, Duration::from_secs(30));
        });
    }
    Ok(status)
}

#[tauri::command]
fn backend_stop(app: AppHandle) -> Result<BackendStatus, String> {
    stop_backend(&app)
}

#[tauri::command]
fn backend_restart(app: AppHandle) -> Result<BackendStatus, String> {
    let status = restart_backend(&app)?;
    if status.state == "starting" {
        let app_for_wait = app.clone();
        thread::spawn(move || {
            let _ = wait_for_backend_ready(&app_for_wait, Duration::from_secs(30));
        });
    }
    Ok(status)
}

fn setup_app(app: &mut tauri::App) -> Result<(), Box<dyn Error>> {
    app.manage(BackendProcess::default());
    let app_handle = app.handle().clone();

    let status = start_backend(&app_handle).map_err(boxed_error)?;
    if status.state == "starting" {
        wait_for_backend_ready(&app_handle, Duration::from_secs(90)).map_err(boxed_error)?;
    }

    let url = APP_URL
        .parse()
        .map_err(|err| boxed_error(format!("invalid app URL: {err}")))?;

    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("MediaProcessPipeline")
        .inner_size(1440.0, 980.0)
        .min_inner_size(1024.0, 720.0)
        .build()
        .map_err(|err| boxed_error(format!("failed to create app window: {err}")))?;

    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            backend_get_status,
            backend_get_logs,
            backend_start,
            backend_stop,
            backend_restart
        ])
        .setup(setup_app)
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                let state = window.state::<BackendProcess>();
                stop_backend_on_close(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running MediaProcessPipeline desktop shell");
}
