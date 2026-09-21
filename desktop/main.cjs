const { app, BrowserWindow, Menu, ipcMain, dialog, shell, screen, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { BackendController, SERVER_URL } = require('./backend.cjs');
const { discoverProject } = require('./project.cjs');
const { isAppUrl, isExternalUrl, isLauncherSender } = require('./security.cjs');

app.setName('MPP Desktop');
app.setAppUserModelId('com.mediaprocesspipeline.desktop');
// Also makes a documented Chromium user-data-dir override apply to our settings.
const userData = app.commandLine.getSwitchValue('user-data-dir');
if (userData) app.setPath('userData', path.resolve(userData));

const launcherFile = path.join(__dirname, 'launcher', 'index.html');
const launcherUrl = pathToFileURL(launcherFile).href;
const iconFile = path.join(__dirname, 'assets', 'icon.png');
let window;
let backend;
let settingsFile;
let logDirectory;
let logFile;
let project = '';
let connecting;
let quitting = false;
let canQuit = false;
let state = { phase: 'starting', title: '正在连接 MPP', message: '正在检查本机服务…', project };

function log(message) {
  if (logFile) fs.appendFileSync(logFile, String(message), 'utf8');
}

function report(error) {
  log(`${new Date().toISOString()} ${error.stack || error}\n`);
  return showLauncher({ phase: 'error', title: '暂时无法启动', message: error.message, details: backend?.tail || '' });
}

function updateState(next) {
  state = { ...state, ...next, project, owned: backend?.owned || false };
  if (window && !window.isDestroyed() && window.webContents.getURL() === launcherUrl) {
    window.webContents.send('desktop:state', state);
  }
  updateMenu();
}

async function showLauncher(next) {
  updateState(next);
  if (window && !window.isDestroyed() && window.webContents.getURL() !== launcherUrl) {
    await window.loadFile(launcherFile);
  }
}

function showWindow() {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

async function openLogs() {
  const error = await shell.openPath(logDirectory);
  if (error) await dialog.showMessageBox(window, { type: 'error', message: '打开日志目录失败', detail: error });
}

async function chooseProject() {
  if (connecting || backend.owned || quitting) return;
  const result = await dialog.showOpenDialog(window, {
    title: '选择已有的 MPP 项目目录', defaultPath: project || undefined,
    properties: ['openDirectory'], buttonLabel: '使用此项目',
  });
  if (result.canceled) return;
  project = result.filePaths[0];
  fs.writeFileSync(settingsFile, `${JSON.stringify({ project }, null, 2)}\n`, 'utf8');
  await connect();
}

function updateMenu() {
  if (!backend) return;
  const controls = [
    { id: 'open-window', label: '打开 MPP', click: showWindow },
    { id: 'open-browser', label: '在浏览器中打开', enabled: state.phase === 'ready', click: () => shell.openExternal(SERVER_URL) },
    { type: 'separator' },
    { id: 'reconnect', label: '重新连接', enabled: !connecting && !quitting, click: () => void connect() },
    { id: 'choose-project', label: '选择项目目录…', enabled: !connecting && !backend.owned && !quitting, click: () => void chooseProject().catch(report) },
    { id: 'open-logs', label: '打开启动日志目录', click: () => void openLogs() },
    { type: 'separator' },
    { id: 'quit-desktop', label: backend.owned ? '退出并停止后端' : '退出 MPP 桌面', click: () => app.quit() },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: '桌面', submenu: controls },
    { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: '视图', submenu: [
      { role: 'reload', label: '刷新' }, { role: 'resetZoom', label: '实际大小' },
      { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' },
      { role: 'togglefullscreen', label: '全屏' },
      ...(!app.isPackaged ? [{ role: 'toggleDevTools', label: '开发者工具' }] : []),
    ] },
  ]));
}

function connect() {
  if (connecting || quitting) return connecting;
  connecting = (async () => {
    await showLauncher({ phase: 'starting', title: '正在启动 MPP', message: '正在连接本机后端，首次加载模型可能需要一些时间。', details: '' });
    if (quitting) return;
    const result = await backend.connect(project);
    if (!result || quitting) return;
    await window.loadURL(SERVER_URL);
    updateState({ phase: 'ready', title: 'MPP 已就绪', message: result.owned ? '关闭窗口将退出应用并停止后端。' : '已连接独立运行的后端。', details: '' });
  })().catch((error) => { if (!quitting) return report(error); }).finally(() => {
    connecting = null;
    updateMenu();
  });
  updateMenu();
  return connecting;
}

function secureWindow() {
  const contents = window.webContents;
  contents.setWindowOpenHandler(({ url }) => {
    if (isExternalUrl(url)) void shell.openExternal(url).catch(log);
    return { action: 'deny' };
  });
  contents.on('will-navigate', (event, url) => {
    if (url === launcherUrl || isAppUrl(url, SERVER_URL)) return;
    event.preventDefault();
    if (isExternalUrl(url)) void shell.openExternal(url).catch(log);
  });
  contents.on('will-redirect', (event, url) => {
    if (!isAppUrl(url, SERVER_URL)) event.preventDefault();
  });
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.on('did-finish-load', () => {
    if (contents.getURL() === launcherUrl) contents.send('desktop:state', state);
  });
  contents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3 && !connecting && !quitting) {
      void report(new Error(`页面加载失败：${description}。请点击重试连接后端。`));
    }
  });
  contents.on('render-process-gone', (_event, details) => {
    if (!quitting) void report(new Error(`页面进程已退出（${details.reason}），请点击重试。`));
  });
}

async function start() {
  fs.mkdirSync(app.getPath('userData'), { recursive: true });
  app.setAppLogsPath();
  logDirectory = app.getPath('logs');
  fs.mkdirSync(logDirectory, { recursive: true });
  logFile = path.join(logDirectory, 'desktop.log');
  if (fs.existsSync(logFile)) {
    fs.copyFileSync(logFile, path.join(logDirectory, 'desktop.previous.log'));
    fs.writeFileSync(logFile, '');
  }
  settingsFile = path.join(app.getPath('userData'), 'desktop-settings.json');
  let saved;
  try { saved = JSON.parse(fs.readFileSync(settingsFile, 'utf8')).project; } catch { /* First launch. */ }
  project = discoverProject({ args: process.argv, saved, appDirectory: __dirname, executableDirectory: path.dirname(app.getPath('exe')) });
  log(`${new Date().toISOString()} MPP Desktop ${app.getVersion()}\nProject: ${project}\n`);
  backend = new BackendController({ log });
  backend.on('ownership-change', updateMenu);
  backend.on('backend-exit', ({ code, signal }) => {
    if (state.phase === 'ready' && !quitting) void report(new Error(`后端已退出（${code ?? signal}）。请查看日志，然后重试。`));
  });
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback) => {
    callback(isAppUrl(contents.getURL(), SERVER_URL) && permission === 'clipboard-sanitized-write');
  });
  session.defaultSession.setPermissionCheckHandler((_contents, permission, origin) => (
    isAppUrl(origin, SERVER_URL) && permission === 'clipboard-sanitized-write'
  ));
  const workArea = screen.getPrimaryDisplay().workAreaSize;
  window = new BrowserWindow({
    title: 'MediaProcessPipeline', width: Math.min(1440, workArea.width), height: Math.min(960, workArea.height),
    minWidth: 800, minHeight: 600, show: false, backgroundColor: '#fafafa', icon: iconFile,
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  secureWindow();
  window.once('ready-to-show', () => window.show());
  window.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    app.quit();
  });
  ipcMain.handle('desktop:state', (event) => {
    if (!isLauncherSender(event, window, launcherUrl)) throw new Error('Unsupported sender');
    return state;
  });
  ipcMain.handle('desktop:action', async (event, action) => {
    if (!isLauncherSender(event, window, launcherUrl)) throw new Error('Unsupported sender');
    if (action === 'retry') return connect();
    if (action === 'choose-project') return chooseProject();
    if (action === 'open-logs') return openLogs();
    if (action === 'quit') app.quit();
  });
  await window.loadFile(launcherFile);
  await connect();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', showWindow);
  app.on('activate', showWindow);
  app.on('before-quit', (event) => {
    if (canQuit) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    updateState({ phase: 'stopping', title: '正在退出 MPP', message: '正在释放后端进程和模型…' });
    // Close our own EventSource/media connections before draining the daemon.
    if (window && !window.isDestroyed()) window.destroy();
    Promise.resolve(backend?.stop()).catch(log).finally(() => {
      canQuit = true;
      app.quit();
    });
  });
  app.whenReady().then(start).catch((error) => {
    dialog.showErrorBox('MPP 启动失败', error.message);
    app.quit();
  });
}
