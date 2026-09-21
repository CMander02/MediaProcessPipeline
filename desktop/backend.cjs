const http = require('node:http');
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const { setTimeout: delay } = require('node:timers/promises');
const { launchSpec } = require('./project.cjs');

const SERVER_URL = 'http://localhost:18000';

function request(url, timeout = 1500) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { agent: false }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        body += chunk;
        if (body.length > 1024 * 1024) req.destroy(new Error('响应过大'));
      });
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, body }));
      response.on('error', reject);
    });
    const timer = setTimeout(() => req.destroy(new Error('连接超时')), timeout);
    req.on('error', reject);
    req.on('close', () => clearTimeout(timer));
  });
}

function refused(error) {
  return error.code === 'ECONNREFUSED'
    || (error.errors?.length > 0 && error.errors.every(refused));
}

async function inspectServer(url = SERVER_URL) {
  let response;
  try {
    response = await request(`${url}/health`);
  } catch (error) {
    return { state: refused(error) ? 'offline' : 'busy' };
  }
  let health;
  try { health = JSON.parse(response.body); } catch { /* Non-MPP listener. */ }
  if (response.status !== 200 || health?.status !== 'healthy'
    || !(health.app === 'mpp' || health.service === 'Media Process Pipeline')) {
    return { state: 'foreign' };
  }
  return { state: 'ready', health };
}

async function checkFrontend(url) {
  const response = await request(`${url}/`);
  if (response.status !== 200 || !response.headers['content-type']?.includes('text/html')
    || !/id=["']root["']/.test(response.body)) {
    throw new Error('后端已连接，前端页面尚未就绪。请在项目的 web 目录执行 npm run build，然后重启该后端。');
  }
}

class BackendController extends EventEmitter {
  constructor({ url = SERVER_URL, startupTimeout = 120000, shutdownTimeout = 22000, launch = launchSpec, log = () => {} } = {}) {
    super();
    Object.assign(this, { url, startupTimeout, shutdownTimeout, launch, log });
    this.child = null;
    this.backendPid = null;
    this.stopping = false;
    this.tail = '';
  }

  get owned() { return this.child !== null; }

  async connect(project) {
    this.stopping = false;
    const existing = await inspectServer(this.url);
    if (this.stopping) return null;
    if (existing.state === 'ready') {
      await checkFrontend(this.url);
      this.log('Connected to existing MPP daemon.\n');
      return { owned: this.owned && existing.health.pid === this.backendPid, ...existing.health };
    }
    if (existing.state !== 'offline') {
      throw new Error(existing.state === 'foreign'
        ? '端口 18000 正被其他服务使用。请释放此端口后重试。'
        : '端口 18000 上的服务暂时没有响应。请等待现有服务启动完成后重试，或检查它的日志。');
    }
    const spec = this.launch(project);
    if (this.stopping) return null;
    this.tail = '';
    this.backendPid = null;
    const child = spawn(spec.command, spec.args, {
      cwd: spec.cwd, windowsHide: true, detached: process.platform !== 'win32',
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    });
    this.child = child;
    this.emit('ownership-change');
    let spawnError;
    child.on('error', (error) => { spawnError = error; });
    child.stdin.on('error', () => {}); // A backend may exit before its stdin is closed.
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    const output = (chunk) => {
      this.log(chunk);
      this.tail = (this.tail + chunk).slice(-8000);
      const pid = this.tail.match(/(?:^|\n)MPP_DESKTOP_PID=(\d+)\r?\n/);
      if (pid) this.backendPid = Number(pid[1]);
    };
    child.stdout.on('data', output);
    child.stderr.on('data', output);
    child.on('exit', (code, signal) => {
      if (this.child === child) this.child = null;
      this.emit('ownership-change');
      this.log(`\nBackend exited: ${code ?? signal}\n`);
      if (!this.stopping) this.emit('backend-exit', { code, signal, tail: this.tail });
    });
    try {
      const deadline = Date.now() + this.startupTimeout;
      while (Date.now() < deadline && !this.stopping) {
        if (spawnError) throw new Error(`启动 Python 失败：${spawnError.message}`);
        if (child.exitCode !== null || child.signalCode !== null) {
          throw new Error(`后端启动时退出（${child.exitCode ?? child.signalCode}）。请查看日志中的错误。`);
        }
        const result = await inspectServer(this.url);
        if (this.stopping) return null;
        if (result.state === 'ready') {
          if (result.health.pid !== this.backendPid || !this.backendPid) {
            throw new Error('启动期间有另一个 MPP 后端占用了端口。请点击重试连接它。');
          }
          await checkFrontend(this.url);
          return { owned: true, ...result.health };
        }
        if (result.state === 'foreign') throw new Error('启动期间端口 18000 被其他服务占用，请释放端口后重试。');
        await delay(300);
      }
      if (!this.stopping) throw new Error(`后端启动超过 ${Math.round(this.startupTimeout / 1000)} 秒。请查看日志和模型配置，再点击重试。`);
      return null;
    } catch (error) {
      await this.stop();
      throw error;
    }
  }

  async stop() {
    this.stopping = true;
    const child = this.child;
    if (!child) return;
    const exited = new Promise((resolve) => {
      if (child.exitCode !== null || child.signalCode !== null || !child.pid) resolve();
      else child.once('exit', resolve);
    });
    child.stdin.end('shutdown\n');
    let timer;
    const stopped = await Promise.race([
      exited.then(() => true),
      new Promise((resolve) => { timer = setTimeout(() => resolve(false), this.shutdownTimeout); }),
    ]);
    clearTimeout(timer);
    if (!stopped && this.child === child) {
      this.log('Backend shutdown timed out; stopping owned process tree.\n');
      if (process.platform === 'win32') {
        await new Promise((resolve) => {
          const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
          killer.once('error', resolve);
          killer.once('exit', resolve);
        });
      } else {
        try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      }
      await exited;
    }
    if (this.child === child) this.child = null;
  }
}

module.exports = { BackendController, SERVER_URL, inspectServer, request };
