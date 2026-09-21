const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { discoverProject, launchSpec } = require('../project.cjs');

test('discovers source checkout and respects explicit/saved project paths', () => {
  const root = path.resolve(__dirname, '../..');
  const options = { args: [], appDirectory: path.join(root, 'desktop'), executableDirectory: 'C:/Elsewhere', env: {} };
  assert.equal(discoverProject(options), root);
  assert.equal(discoverProject({ ...options, saved: 'D:/项目 🎧' }), 'D:/项目 🎧');
  assert.equal(discoverProject({ ...options, args: ['--project', 'D:/项目 🎧'], saved: root }), path.resolve('D:/项目 🎧'));
  assert.equal(discoverProject({ ...options, args: ['--project=D:/Other'] }), path.resolve('D:/Other'));
});

test('explains missing project, Python, build and launcher requirements', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mpp-桌面 🎧-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.throws(() => launchSpec(root), /请选择 MPP 项目/);
  fs.mkdirSync(path.join(root, 'backend/app'), { recursive: true });
  fs.writeFileSync(path.join(root, 'pyproject.toml'), '');
  fs.writeFileSync(path.join(root, 'backend/app/main.py'), '');
  assert.throws(() => launchSpec(root), /uv sync/);
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(python, '');
  assert.throws(() => launchSpec(root), /npm run build/);
  fs.mkdirSync(path.join(root, 'web/dist'), { recursive: true });
  fs.writeFileSync(path.join(root, 'web/dist/index.html'), '');
  assert.throws(() => launchSpec(root), /desktop.py/);
  fs.writeFileSync(path.join(root, 'backend/app/desktop.py'), '');
  assert.deepEqual(launchSpec(root), { command: python, args: ['-m', 'app.desktop'], cwd: path.join(root, 'backend') });
});

test('portable EXE discovers the project outside its extraction directory', () => {
  const root = path.resolve(__dirname, '../..');
  for (const directory of [root, path.join(root, 'desktop/dist')]) {
    assert.equal(discoverProject({
      args: [], appDirectory: path.join(os.tmpdir(), 'mpp-extracted/resources/app.asar'),
      executableDirectory: path.join(os.tmpdir(), 'mpp-extracted'),
      env: { PORTABLE_EXECUTABLE_DIR: directory },
    }), root);
  }
});
