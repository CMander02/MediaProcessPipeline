const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const BACKEND_PORT = 18000;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

const PROJECT_ROOT = "C:\\Users\\cmander\\Toolbox\\AI\\MediaProcessPipeline";

let backendProcess = null;
let mainWindow = null;

function startBackend() {
  const backendDir = path.join(PROJECT_ROOT, "backend");
  console.log(`[electron] PROJECT_ROOT: ${PROJECT_ROOT}`);
  console.log(`[electron] backendDir: ${backendDir}`);

  backendProcess = spawn("uv", ["run", "python", "-m", "app.cli", "serve"], {
    cwd: backendDir,
    stdio: "pipe",
    shell: true,
    windowsHide: true,
  });

  backendProcess.stdout.on("data", (data) => {
    console.log(`[backend] ${data.toString().trim()}`);
  });

  backendProcess.stderr.on("data", (data) => {
    console.log(`[backend] ${data.toString().trim()}`);
  });

  backendProcess.on("error", (err) => {
    console.error("Failed to start backend:", err);
  });

  backendProcess.on("exit", (code) => {
    console.log(`Backend exited with code ${code}`);
    backendProcess = null;
  });
}

function stopBackend() {
  const proc = backendProcess;
  backendProcess = null;

  if (process.platform === "win32") {
    // Kill whatever is listening on the backend port — this reliably kills
    // the Python process even when spawned through a shell/cmd wrapper.
    try {
      require("child_process").execSync(
        `for /f "tokens=5" %a in ('netstat -aon ^| findstr :${BACKEND_PORT} ^| findstr LISTENING') do taskkill /f /pid %a`,
        { shell: true, windowsHide: true, stdio: "ignore" }
      );
    } catch {}
  }

  // Also kill the shell wrapper if it's still alive
  if (proc && proc.pid) {
    try {
      spawn("taskkill", ["/pid", proc.pid.toString(), "/f", "/t"], {
        shell: true,
        windowsHide: true,
      });
    } catch {}
  }
}

function waitForBackend(retries = 30) {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    function check() {
      const req = http.get(`${BACKEND_URL}/health`, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else {
          retry();
        }
      });
      req.on("error", retry);
      req.setTimeout(1000, retry);
    }

    function retry() {
      attempts++;
      if (attempts >= retries) {
        reject(new Error("Backend failed to start"));
        return;
      }
      setTimeout(check, 500);
    }

    check();
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: "MediaProcessPipeline",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
  });

  mainWindow.loadURL(BACKEND_URL);

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  // Open external links in system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  startBackend();

  try {
    await waitForBackend();
  } catch {
    console.error("Backend did not start in time");
    app.quit();
    return;
  }

  createWindow();
});

app.on("window-all-closed", () => {
  stopBackend();
  app.quit();
});

app.on("before-quit", () => {
  stopBackend();
});
