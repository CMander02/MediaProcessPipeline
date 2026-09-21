const { spawn } = require('node:child_process');
const path = require('node:path');
const desktop = path.resolve(__dirname, '..');
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [desktop, '--project', path.dirname(desktop)], {
  cwd: desktop, detached: true, windowsHide: true, stdio: 'ignore', env,
});
child.on('error', (error) => { console.error(error.message); process.exitCode = 1; });
child.unref();
