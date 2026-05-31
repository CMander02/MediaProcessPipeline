const { app, BrowserWindow, dialog, ipcMain } = require("electron")
const { spawn } = require("node:child_process")
const path = require("node:path")

const BACKEND_URL = "http://127.0.0.1:18000"
const BACKEND_COMMAND = "uv run python -m app.cli serve"
const MAX_LOG_LINES = 1200

let mainWindow = null
let backendProcess = null
let backendOwned = false
let backendStatus = {
  state: "stopped",
  command: BACKEND_COMMAND,
  cwd: getBackendCwd(),
  pid: null,
  url: BACKEND_URL,
  message: "未启动",
}
const logs = []

function getBackendCwd() {
  return path.resolve(app.getAppPath(), "..", "backend")
}

function appendLog(source, text) {
  const lines = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")
  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    if (!line) continue
    const entry = {
      ts: new Date().toISOString(),
      source,
      line,
    }
    logs.push(entry)
    if (logs.length > MAX_LOG_LINES) logs.shift()
    broadcast("mpp-backend:log", entry)
  }
}

function setStatus(patch) {
  backendStatus = {
    ...backendStatus,
    cwd: getBackendCwd(),
    ...patch,
  }
  broadcast("mpp-backend:status", backendStatus)
  return backendStatus
}

function broadcast(channel, payload) {
  BrowserWindow.getAllWindows().forEach((win) => {
    if (!win.isDestroyed()) win.webContents.send(channel, payload)
  })
}

async function isBackendHealthy() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(1200) })
    return response.ok
  } catch {
    return false
  }
}

async function waitForBackend(timeoutMs = 30000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (await isBackendHealthy()) return true
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return false
}

async function startBackend() {
  if (backendProcess) return backendStatus

  if (await isBackendHealthy()) {
    backendOwned = false
    appendLog("system", "Detected an existing backend on 127.0.0.1:18000; Electron will reuse it.")
    return setStatus({
      state: "external",
      pid: null,
      message: "检测到 18000 端口已有后端服务，Electron 将复用它。",
    })
  }

  const backendCwd = getBackendCwd()
  appendLog("system", `Starting backend: ${BACKEND_COMMAND}`)
  appendLog("system", `Working directory: ${backendCwd}`)
  setStatus({ state: "starting", pid: null, message: "正在启动后端..." })

  backendOwned = true
  backendProcess = spawn("uv", ["run", "python", "-u", "-m", "app.cli", "serve"], {
    cwd: backendCwd,
    windowsHide: true,
    shell: process.platform === "win32",
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONUNBUFFERED: "1",
      NO_COLOR: "1",
    },
  })

  setStatus({
    state: "starting",
    pid: backendProcess.pid ?? null,
    message: "后端进程已创建，等待健康检查通过。",
  })

  backendProcess.stdout?.on("data", (chunk) => appendLog("stdout", chunk))
  backendProcess.stderr?.on("data", (chunk) => appendLog("stderr", chunk))

  backendProcess.on("error", (error) => {
    appendLog("error", error.message)
    backendProcess = null
    backendOwned = false
    setStatus({ state: "error", pid: null, message: error.message })
  })

  backendProcess.on("exit", (code, signal) => {
    appendLog("system", `Backend exited with code ${code ?? "null"} signal ${signal ?? "null"}`)
    backendProcess = null
    backendOwned = false
    setStatus({
      state: code === 0 ? "stopped" : "error",
      pid: null,
      message: code === 0 ? "后端已停止。" : `后端异常退出，code=${code ?? "null"} signal=${signal ?? "null"}`,
    })
  })

  waitForBackend().then((ready) => {
    if (!backendProcess) return
    if (ready) {
      appendLog("system", "Backend health check passed.")
      setStatus({
        state: "running",
        pid: backendProcess.pid ?? null,
        message: "后端已就绪。",
      })
      return
    }
    setStatus({
      state: "starting",
      pid: backendProcess.pid ?? null,
      message: "后端仍在启动，日志里可能有模型加载或依赖初始化信息。",
    })
    appendLog("system", "Backend health check did not pass within the initial wait window.")
  })

  return backendStatus
}

async function stopBackend() {
  if (!backendProcess) {
    if (backendStatus.state === "external") {
      return setStatus({ state: "external", message: "当前后端不是 Electron 启动的，不会停止外部进程。" })
    }
    return setStatus({ state: "stopped", pid: null, message: "后端未运行。" })
  }

  if (!backendOwned) {
    return setStatus({ state: "external", message: "当前后端不是 Electron 启动的，不会停止外部进程。" })
  }

  const pid = backendProcess.pid
  appendLog("system", `Stopping backend process ${pid}`)
  setStatus({ state: "stopping", pid: pid ?? null, message: "正在停止后端..." })

  if (process.platform === "win32" && pid) {
    spawn("taskkill", ["/pid", String(pid), "/t", "/f"], { windowsHide: true })
  } else {
    backendProcess.kill("SIGTERM")
  }

  return backendStatus
}

async function restartBackend() {
  await stopBackend()
  const started = Date.now()
  while (backendProcess && Date.now() - started < 8000) {
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  return startBackend()
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 760,
    minWidth: 980,
    minHeight: 560,
    title: "Media Process Pipeline",
    backgroundColor: "#ffffff",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  mainWindow.setMenuBarVisibility(false)

  await startBackend()
  if (await waitForBackend(15000)) {
    await mainWindow.loadURL(BACKEND_URL)
  } else {
    await mainWindow.loadFile(path.join(app.getAppPath(), "dist", "index.html"), { hash: "/backend" })
  }

  mainWindow.on("closed", () => {
    mainWindow = null
  })
}

app.whenReady().then(createWindow)

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})

app.on("before-quit", () => {
  if (backendOwned && backendProcess) {
    stopBackend()
  }
})

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit()
})

ipcMain.handle("mpp-backend:get-status", () => backendStatus)
ipcMain.handle("mpp-backend:get-logs", () => logs)
ipcMain.handle("mpp-backend:start", () => startBackend())
ipcMain.handle("mpp-backend:stop", () => stopBackend())
ipcMain.handle("mpp-backend:restart", () => restartBackend())
ipcMain.handle("mpp-dialog:select-directory", async (_event, options = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: options.title || "选择文件夹",
    defaultPath: options.defaultPath || undefined,
    properties: ["openDirectory"],
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths[0]
})
