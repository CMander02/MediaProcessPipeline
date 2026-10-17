const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
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

async function main() {
  const mac = process.platform === 'darwin';
  const portable = process.argv.includes('--portable');
  if (mac && portable) throw new Error('Portable EXE builds are Windows-only; use npm run pack or npm run dist on macOS.');
  // Keep unpacked Electron files and builder diagnostics out of the project.
  const output = portable ? fs.mkdtempSync(path.join(os.tmpdir(), 'mpp-desktop-build-')) : null;
  try {
    await build({
      projectDir: desktop,
      targets: mac
        ? Platform.MAC.createTarget(process.argv.includes('--dir') ? ['dir'] : ['dmg'], process.arch === 'arm64' ? Arch.arm64 : Arch.x64)
        : Platform.WINDOWS.createTarget(process.argv.includes('--dir') ? ['dir'] : portable ? ['portable'] : ['nsis'], Arch.x64),
      config: output ? {
        ...config,
        directories: { ...config.directories, output },
        portable: { ...config.portable, artifactName: 'MPP.exe' },
      } : config,
      publish: 'never',
    });
    if (output) {
      const destination = path.join(root, 'MPP.exe');
      fs.copyFileSync(path.join(output, 'MPP.exe'), destination);
      console.log(`Portable app ready: ${destination}`);
    }
  } finally {
    if (output && path.dirname(output) === path.resolve(os.tmpdir())) {
      fs.rmSync(output, { recursive: true, force: true });
    }
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
