const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { BackendController, inspectServer } = require('../backend.cjs');

async function listener(handler) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, 'localhost', resolve));
  return { server, url: `http://localhost:${server.address().port}`, port: server.address().port };
}

async function fixture(t, mode = '') {
  const endpoint = await listener(() => {});
  await new Promise((resolve) => endpoint.server.close(resolve));
  const controller = new BackendController({
    url: endpoint.url, startupTimeout: mode === 'timeout' ? 500 : 5000,
    shutdownTimeout: mode === 'stubborn' ? 100 : 1000,
    launch: () => ({ command: process.execPath, args: [path.join(__dirname, 'fixtures', 'daemon.cjs'), String(endpoint.port), mode] }),
  });
  t.after(() => controller.stop());
  return controller;
}

test('starts, reuses, and gracefully stops its owned daemon', async (t) => {
  const controller = await fixture(t);
  const result = await controller.connect('');
  assert.equal(result.owned, true);
  assert.equal(result.pid, controller.backendPid);
  assert.equal((await controller.connect('')).owned, true);
  await controller.stop();
  assert.equal(controller.owned, false);
  assert.equal((await inspectServer(controller.url)).state, 'offline');
});

test('leaves an existing standalone daemon running on exit', async (t) => {
  const owner = await fixture(t);
  await owner.connect('');
  const guest = new BackendController({ url: owner.url, launch: () => { throw new Error('must not spawn'); } });
  assert.equal((await guest.connect('')).owned, false);
  await guest.stop();
  assert.equal((await inspectServer(owner.url)).state, 'ready');
  assert.equal(owner.owned, true);
});

test('rejects foreign services and missing frontend without spawning', async (t) => {
  let health = false;
  const endpoint = await listener((request, response) => {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(health && request.url === '/health' ? { status: 'healthy', service: 'Media Process Pipeline' } : {}));
  });
  t.after(() => endpoint.server.close());
  const controller = new BackendController({ url: endpoint.url, launch: () => { throw new Error('must not spawn'); } });
  await assert.rejects(controller.connect(''), /其他服务/);
  health = true;
  await assert.rejects(controller.connect(''), /前端页面尚未就绪/);
  assert.equal(controller.owned, false);
});

test('reports a backend startup crash and allows retry', async (t) => {
  const controller = await fixture(t, 'crash');
  await assert.rejects(controller.connect(''), /启动时退出/);
  assert.equal(controller.owned, false);
  const launch = controller.launch;
  controller.launch = () => { const spec = launch(); spec.args.pop(); return spec; };
  assert.equal((await controller.connect('')).owned, true);
});

test('cleans up startup timeouts and racing daemon identities', async (t) => {
  for (const mode of ['timeout', 'wrong-pid']) {
    const controller = await fixture(t, mode);
    await assert.rejects(controller.connect(''), mode === 'timeout' ? /启动超过/ : /另一个 MPP 后端/);
    assert.equal(controller.owned, false);
    assert.equal((await inspectServer(controller.url)).state, 'offline');
  }
});

test('stops an owned process tree when graceful shutdown stalls', async (t) => {
  const controller = await fixture(t, 'stubborn');
  await controller.connect('');
  const descendant = Number(controller.tail.match(/MPP_FIXTURE_CHILD=(\d+)/)[1]);
  await controller.stop();
  assert.equal(controller.owned, false);
  assert.equal((await inspectServer(controller.url)).state, 'offline');
  assert.throws(() => process.kill(descendant, 0), { code: 'ESRCH' });
});

test('cancel during startup leaves no daemon', async (t) => {
  const controller = await fixture(t, 'timeout');
  const connection = controller.connect('');
  await new Promise((resolve) => setTimeout(resolve, 100));
  await controller.stop();
  assert.equal(await connection, null);
  assert.equal(controller.owned, false);
});

test('spawn failure is reported and cleaned up', async (t) => {
  const controller = await fixture(t);
  controller.launch = () => ({ command: path.join(__dirname, 'missing-python'), args: [] });
  await assert.rejects(controller.connect(''), /启动 Python 失败/);
  assert.equal(controller.owned, false);
});
