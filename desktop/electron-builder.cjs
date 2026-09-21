const fs = require('node:fs');
const path = require('node:path');

const pyproject = fs.readFileSync(path.join(__dirname, '..', 'pyproject.toml'), 'utf8');
const version = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (!version) throw new Error('Cannot read release version from pyproject.toml');

module.exports = {
  appId: 'com.mediaprocesspipeline.desktop',
  productName: 'MPP Desktop',
  extraMetadata: { version },
  asar: true,
  directories: { output: 'dist' },
  files: ['*.cjs', '!electron-builder.cjs', 'launcher/**', 'assets/icon.png', 'package.json'],
  win: { target: [{ target: 'nsis', arch: ['x64'] }], icon: 'assets/icon.png' },
  portable: { artifactName: 'MPP-Desktop-Portable.${ext}' },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    runAfterFinish: false,
    artifactName: 'MPP-Desktop-${version}-Setup.${ext}',
  },
};
