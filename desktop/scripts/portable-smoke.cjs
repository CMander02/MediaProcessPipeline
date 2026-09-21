// NSIS's portable bootstrap does not forward inspector stderr to Playwright.
// Connect to explicit local debug ports while launching the actual single EXE.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('playwright-core');
const { inspectServer } = require('../backend.cjs');

const root = path.resolve(__dirname, '../..');
const output = path.join(root, 'output/desktop-portable-smoke', String(Date.now()));
const project = path.join(output, '项目 🎧');
let child, browser, socket;

async function until(callback, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await callback();
    if (value) return value;
    await delay(200);
  }
  throw new Error('Portable smoke condition timed out');
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, 'localhost', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function main() {
  assert.equal((await inspectServer()).state, 'offline', 'Leave an existing daemon untouched.');
  fs.mkdirSync(project, { recursive: true });
  fs.cpSync(path.join(root, 'backend/app'), path.join(project, 'backend/app'), {
    recursive: true, filter: (source) => path.basename(source) !== '__pycache__',
  });
  fs.copyFileSync(path.join(root, 'pyproject.toml'), path.join(project, 'pyproject.toml'));
  fs.mkdirSync(path.join(project, 'web'), { recursive: true });
  fs.symlinkSync(path.join(root, '.venv'), path.join(project, '.venv'), 'junction');
  fs.symlinkSync(path.join(root, 'web/dist'), path.join(project, 'web/dist'), 'junction');
  fs.writeFileSync(path.join(project, 'config.json'), JSON.stringify({
    data_root: path.join(project, 'library'), asr_provider: 'siliconflow',
    ytdlp_auto_update: false, enable_diarization: false, enable_voiceprint: false,
  }));
  const exe = path.join(project, 'MPP-Desktop-Portable.exe');
  fs.copyFileSync(path.join(root, 'desktop/dist/MPP-Desktop-Portable.exe'), exe);
  const inspectorPort = await freePort();
  const browserPort = await freePort();
  child = spawn(exe, [
    `--inspect=${inspectorPort}`, `--remote-debugging-port=${browserPort}`,
    `--user-data-dir=${path.join(output, 'profile')}`,
  ], { windowsHide: true, stdio: 'ignore' });
  const exited = new Promise((resolve) => child.once('exit', resolve));
  const targets = await until(async () => {
    try { return await (await fetch(`http://localhost:${inspectorPort}/json/list`)).json(); } catch { return null; }
  });
  socket = new WebSocket(targets[0].webSocketDebuggerUrl.replace('127.0.0.1', 'localhost'));
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let commandId = 0;
  async function evaluate(expression) {
    const id = ++commandId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Inspector request timed out')), 10000);
      const onMessage = (event) => {
        const response = JSON.parse(event.data);
        if (response.id !== id) return;
        clearTimeout(timer);
        socket.removeEventListener('message', onMessage);
        if (response.error || response.result?.exceptionDetails) reject(new Error(JSON.stringify(response)));
        else resolve(response.result.result.value);
      };
      socket.addEventListener('message', onMessage);
      socket.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, returnByValue: true } }));
    });
  }
  const health = await until(async () => (await inspectServer()).health);
  browser = await chromium.connectOverCDP(`http://localhost:${browserPort}`);
  const page = browser.contexts()[0].pages()[0];
  await page.waitForURL('http://localhost:18000/');
  await page.getByRole('button', { name: '处理', exact: true }).or(page.getByRole('link', { name: '处理', exact: true })).first().waitFor();
  const electron = 'process.mainModule.require("electron")';
  const info = await evaluate(`JSON.stringify({directory:process.env.PORTABLE_EXECUTABLE_DIR,appPath:${electron}.app.getAppPath(),version:${electron}.app.getVersion()})`);
  const metadata = JSON.parse(info);
  assert.equal(path.resolve(metadata.directory), project);
  assert.ok(!metadata.appPath.startsWith(project));
  await page.screenshot({ path: path.join(output, 'portable.png') });
  assert.equal(await page.evaluate(() => typeof window.mppDesktop), 'undefined');
  await evaluate(`${electron}.BrowserWindow.getAllWindows()[0].minimize(); true`);
  assert.equal(await evaluate(`${electron}.BrowserWindow.getAllWindows()[0].isMinimized()`), true);
  assert.equal((await inspectServer()).health.pid, health.pid);
  await evaluate(`${electron}.Menu.getApplicationMenu().getMenuItemById('open-window').click(); true`);
  assert.equal(await evaluate(`${electron}.BrowserWindow.getAllWindows()[0].isVisible()`), true);
  await evaluate(`setTimeout(() => ${electron}.BrowserWindow.getAllWindows()[0].close(), 50); true`);
  socket.close();
  await browser.close();
  browser = null;
  await exited;
  assert.equal(child.exitCode, 0);
  assert.equal((await inspectServer()).state, 'offline');
  const log = fs.readFileSync(path.join(output, 'profile/logs/desktop.log'), 'utf8');
  assert.match(log, /Backend exited: 0/);
  const checks = ['single EXE extraction', 'automatic project discovery with Unicode path', 'real React page', 'renderer sandbox', 'minimize keeps backend', 'window restore', 'window close gracefully exits backend and portable bootstrap'];
  fs.writeFileSync(path.join(output, 'checks.json'), JSON.stringify({ metadata, checks }, null, 2));
  console.log(`PASS ${checks.join('; ')}\nEvidence: ${output}`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  socket?.close();
  await browser?.close().catch(() => {});
  if (child && child.exitCode === null) {
    await new Promise((resolve) => {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      killer.once('exit', resolve);
    });
  }
  for (const link of [path.join(project, '.venv'), path.join(project, 'web/dist')]) {
    if (fs.lstatSync(link, { throwIfNoEntry: false })?.isSymbolicLink()) fs.unlinkSync(link);
  }
});
