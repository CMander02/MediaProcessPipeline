// Actual Electron + FastAPI acceptance checks, with a disposable data library.
// Uses the project's uv environment; never stops a pre-existing service.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { _electron: electron } = require('playwright-core');
const { inspectServer, SERVER_URL, request } = require('../backend.cjs');
const { argumentValue } = require('../project.cjs');

const desktop = path.resolve(__dirname, '..');
const root = path.dirname(desktop);
const packaged = argumentValue(process.argv, '--packaged');
const executable = packaged ? path.resolve(packaged) : require('electron');
const output = path.join(root, 'output', 'desktop-smoke', `${Date.now()}-${packaged ? 'packaged' : 'source'}`);
const project = path.join(output, '项目 🎧');
const userData = path.join(output, 'profile');
const checks = [];
const applications = new Set();
let standalone;
let foreign;

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log(`PASS ${name}`);
  fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify(checks, null, 2));
}

async function waitUntil(predicate, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await delay(200);
  }
  throw new Error('Condition timed out');
}

function args(selectedProject = project) {
  return [...(packaged ? [] : [desktop]), `--user-data-dir=${userData}`, '--project', selectedProject];
}

async function launch(selectedProject = project, includeProject = true) {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const application = await electron.launch({
    executablePath: executable,
    args: includeProject ? args(selectedProject) : [...(packaged ? [] : [desktop]), `--user-data-dir=${userData}`],
    cwd: desktop, env, timeout: 30000,
  });
  applications.add(application);
  application.process().stderr.on('data', (data) => fs.appendFileSync(path.join(output, 'electron-stderr.log'), data));
  const page = await application.firstWindow();
  page.on('pageerror', (error) => fs.appendFileSync(path.join(output, 'renderer-errors.log'), `${error.stack}\n`));
  return { application, page };
}

async function quit(application) {
  const exited = application.waitForEvent('close', { timeout: 35000 });
  await application.evaluate(({ Menu }) => Menu.getApplicationMenu().getMenuItemById('quit-desktop').click()).catch(() => {});
  await exited;
  applications.delete(application);
}

async function ready(page) {
  await page.waitForURL(`${SERVER_URL}/`, { timeout: 130000 });
  await page.getByRole('button', { name: '处理', exact: true }).or(page.getByRole('link', { name: '处理', exact: true })).first().waitFor({ timeout: 30000 });
}

async function killTree(pid) {
  await new Promise((resolve, reject) => {
    const child = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    child.once('error', reject);
    child.once('exit', resolve);
  });
}

async function sse() {
  return new Promise((resolve, reject) => {
    const req = http.get(`${SERVER_URL}/api/tasks/events`, (response) => {
      try {
        assert.equal(response.statusCode, 200);
        assert.match(response.headers['content-type'], /text\/event-stream/);
        response.once('data', (chunk) => { resolve(String(chunk)); req.destroy(); });
      } catch (error) { req.destroy(); reject(error); }
    });
    req.on('error', reject);
    const timer = setTimeout(() => { req.destroy(); reject(new Error('SSE timeout')); }, 35000);
    req.on('close', () => clearTimeout(timer));
  });
}

async function main() {
  assert.equal(process.platform, 'win32', 'Windows smoke test');
  assert.equal((await inspectServer()).state, 'offline', 'Port 18000 must be free; leave existing daemon untouched.');
  fs.mkdirSync(project, { recursive: true });
  fs.cpSync(path.join(root, 'backend', 'app'), path.join(project, 'backend', 'app'), {
    recursive: true, filter: (source) => path.basename(source) !== '__pycache__',
  });
  fs.copyFileSync(path.join(root, 'pyproject.toml'), path.join(project, 'pyproject.toml'));
  fs.mkdirSync(path.join(project, 'web'), { recursive: true });
  fs.symlinkSync(path.join(root, '.venv'), path.join(project, '.venv'), 'junction');
  fs.symlinkSync(path.join(root, 'web', 'dist'), path.join(project, 'web', 'dist'), 'junction');
  fs.writeFileSync(path.join(project, 'config.json'), JSON.stringify({
    data_root: path.join(project, 'library'), asr_provider: 'siliconflow',
    ytdlp_auto_update: false, enable_diarization: false, enable_voiceprint: false,
  }));

  let { application, page } = await launch(path.join(output, 'missing-project'));
  await page.getByRole('heading', { name: '暂时无法启动' }).waitFor();
  assert.match(await page.locator('#message').textContent(), /请选择 MPP 项目目录/);
  assert.equal(await page.evaluate(() => typeof require), 'undefined');
  await page.screenshot({ path: path.join(output, 'startup-error.png') });
  pass('startup error screen and renderer sandbox');

  // Exercise the actual native-picker result flow without an unattended OS dialog.
  await application.evaluate(({ dialog }, selected) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] });
  }, project);
  await page.getByRole('button', { name: '选择项目目录' }).click();
  await ready(page);
  const health = (await inspectServer()).health;
  assert.equal(health.app, 'mpp');
  assert.equal(JSON.parse(fs.readFileSync(path.join(userData, 'desktop-settings.json'))).project, project);
  assert.equal(await page.evaluate(() => typeof window.mppDesktop), 'undefined');
  assert.equal(await page.evaluate(() => typeof require), 'undefined');
  assert.equal(await application.evaluate(({ app }) => app.isPackaged), Boolean(packaged));
  pass('project selection, Unicode paths, owned FastAPI startup and real React page', { backendPid: health.pid });

  const api = await page.evaluate(async () => ({
    tasks: (await fetch('/api/tasks')).status,
    capabilities: (await fetch('/api/capabilities')).status,
  }));
  assert.deepEqual(api, { tasks: 200, capabilities: 200 });
  const stream = await sse();
  pass('same-origin API and live SSE', { stream: stream.slice(0, 150) });
  await page.screenshot({ path: path.join(output, 'files.png') });
  const processLink = page.getByRole('button', { name: '处理', exact: true }).or(page.getByRole('link', { name: '处理', exact: true })).first();
  await processLink.click();
  await page.screenshot({ path: path.join(output, 'submit.png') });
  pass('existing frontend navigation');

  await application.evaluate(({ app, shell }) => {
    app.smokeOpenedUrls = [];
    shell.openExternal = async (url) => { app.smokeOpenedUrls.push(url); };
  });
  await page.evaluate(() => {
    window.open('https://example.com/mpp-smoke', '_blank');
    window.open('file:///C:/Windows', '_blank');
  });
  await waitUntil(() => application.evaluate(({ app }) => app.smokeOpenedUrls.length === 1));
  assert.deepEqual(await application.evaluate(({ app }) => app.smokeOpenedUrls), ['https://example.com/mpp-smoke']);
  assert.ok(page.url().startsWith(SERVER_URL));
  assert.equal(await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  pass('actual popup handling opens HTTP links externally and blocks file URLs');

  const opened = await application.evaluate(async ({ shell, Menu }) => {
    let openedPath;
    shell.openPath = async (value) => { openedPath = value; return ''; };
    Menu.getApplicationMenu().getMenuItemById('open-logs').click();
    return openedPath;
  });
  assert.ok(fs.existsSync(path.join(opened, 'desktop.log')));
  pass('startup log menu resolves an existing log file');

  await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].minimize());
  assert.equal(await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isMinimized()), true);
  assert.equal((await inspectServer()).health.pid, health.pid);
  const second = spawn(executable, args(), { windowsHide: true, stdio: 'ignore' });
  await new Promise((resolve, reject) => { second.once('exit', resolve); second.once('error', reject); });
  await waitUntil(() => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()));
  assert.equal(await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  assert.equal((await inspectServer()).health.pid, health.pid);
  pass('minimize keeps backend, single instance and window restore');

  // An independent browser can leave a long-lived SSE response open on exit.
  const heldStream = await new Promise((resolve, reject) => {
    const client = http.get(`${SERVER_URL}/api/tasks/events`, () => resolve(client));
    client.on('error', reject);
  });
  const windowClosed = application.waitForEvent('close', { timeout: 20000 });
  await application.evaluate(({ BrowserWindow }) => { setTimeout(() => BrowserWindow.getAllWindows()[0].close(), 50); return true; });
  await windowClosed;
  applications.delete(application);
  await waitUntil(async () => (await inspectServer()).state === 'offline');
  heldStream.destroy();
  assert.equal((await inspectServer()).state, 'offline');
  const shutdownLog = fs.readFileSync(path.join(userData, 'logs', 'desktop.log'), 'utf8');
  assert.match(shutdownLog, /Application shutdown complete/);
  assert.match(shutdownLog, /Backend exited: 0/);
  fs.writeFileSync(path.join(output, 'graceful-shutdown.log'), shutdownLog);
  pass('window close exits desktop and gracefully stops backend with an external SSE client');

  ({ application, page } = await launch(project, false));
  await ready(page);
  pass('saved project used on next launch');
  // app.exit skips before-quit, testing the stdin EOF lifetime independently.
  const abruptlyClosed = application.waitForEvent('close', { timeout: 10000 });
  await application.evaluate(({ app }) => app.exit(0)).catch(() => {});
  await abruptlyClosed;
  applications.delete(application);
  await waitUntil(async () => (await inspectServer()).state === 'offline');
  pass('abrupt desktop exit cleans up backend through parent EOF');

  const standaloneOutput = fs.openSync(path.join(output, 'standalone.log'), 'a');
  standalone = spawn(path.join(root, '.venv', 'Scripts', 'python.exe'), ['-m', 'app.cli', 'serve'], {
    cwd: path.join(project, 'backend'), windowsHide: true, stdio: ['ignore', standaloneOutput, standaloneOutput],
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  });
  fs.closeSync(standaloneOutput);
  await waitUntil(async () => (await inspectServer()).state === 'ready', 120000);
  const standalonePid = (await inspectServer()).health.pid;
  ({ application, page } = await launch());
  await ready(page);
  assert.equal(await application.evaluate(({ Menu }) => Menu.getApplicationMenu().getMenuItemById('quit-desktop').label), '退出 MPP 桌面');
  await quit(application);
  assert.equal((await inspectServer()).health.pid, standalonePid);
  pass('existing CLI daemon survives desktop exit', { backendPid: standalonePid });
  await killTree(standalone.pid);
  standalone = null;
  await waitUntil(async () => (await inspectServer()).state === 'offline');

  foreign = http.createServer((_req, response) => response.end('another service'));
  await new Promise((resolve) => foreign.listen(18000, 'localhost', resolve));
  ({ application, page } = await launch());
  await page.getByRole('heading', { name: '暂时无法启动' }).waitFor();
  assert.match(await page.locator('#message').textContent(), /其他服务/);
  pass('foreign port collision produces actionable error');
  await new Promise((resolve) => foreign.close(resolve));
  foreign = null;
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await ready(page);
  pass('retry recovers after releasing a conflicting port');
  await killTree((await inspectServer()).health.pid);
  await page.getByRole('heading', { name: '暂时无法启动' }).waitFor();
  assert.match(await page.locator('#message').textContent(), /后端已退出/);
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await ready(page);
  pass('unexpected backend exit is reported and retry starts a new daemon');
  await quit(application);
  assert.equal((await inspectServer()).state, 'offline');
  pass('final cleanup leaves port free');
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  for (const application of applications) {
    try { await quit(application); } catch { await application.close().catch(() => {}); }
  }
  if (standalone && standalone.exitCode === null) await killTree(standalone.pid);
  if (foreign) await new Promise((resolve) => foreign.close(resolve));
  // Remove only these junctions, preserving the shared environment and web build.
  for (const link of [path.join(project, '.venv'), path.join(project, 'web', 'dist')]) {
    if (fs.lstatSync(link, { throwIfNoEntry: false })?.isSymbolicLink()) fs.unlinkSync(link);
  }
  console.log(`Evidence: ${output}`);
});
