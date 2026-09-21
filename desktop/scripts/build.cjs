const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { build, Platform, Arch } = require('electron-builder');
const config = require('../electron-builder.cjs');

const desktop = path.resolve(__dirname, '..');
const root = path.dirname(desktop);
const npm = process.env.npm_execpath;
if (!npm) throw new Error('Run this build through npm run pack, npm run portable or npm run dist.');
const frontend = spawnSync(process.execPath, [npm, 'run', 'build'], { cwd: path.join(root, 'web'), stdio: 'inherit', windowsHide: true });
if (frontend.error) throw frontend.error;
if (frontend.status !== 0) process.exit(frontend.status || 1);
fs.copyFileSync(path.join(root, 'web', 'public', 'pwa-512x512.png'), path.join(desktop, 'assets', 'icon.png'));

build({
  projectDir: desktop,
  targets: Platform.WINDOWS.createTarget(process.argv.includes('--dir') ? ['dir'] : process.argv.includes('--portable') ? ['portable'] : ['nsis'], Arch.x64),
  config,
  publish: 'never',
}).catch((error) => { console.error(error); process.exitCode = 1; });
