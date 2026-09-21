const fs = require('node:fs');
const path = require('node:path');

function isProject(directory) {
  return typeof directory === 'string'
    && fs.existsSync(path.join(directory, 'pyproject.toml'))
    && fs.existsSync(path.join(directory, 'backend', 'app', 'main.py'));
}

function argumentValue(args, name) {
  const entry = args.find((arg) => arg.startsWith(`${name}=`));
  if (entry) return entry.slice(name.length + 1);
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

function discoverProject({ args, saved, appDirectory, executableDirectory, env = process.env }) {
  const explicit = argumentValue(args, '--project') || env.MPP_PROJECT_DIR;
  if (explicit) return path.resolve(explicit);
  // A saved selection remains visible when a drive is disconnected/moved.
  if (saved) return saved;
  // A portable EXE extracts Electron into a temporary directory. Use the
  // original EXE location to also find projects from desktop/dist/.
  const portableDirectory = env.PORTABLE_EXECUTABLE_DIR;
  const candidates = [
    ...(portableDirectory ? [portableDirectory, path.dirname(portableDirectory), path.dirname(path.dirname(portableDirectory))] : []),
    path.dirname(appDirectory), executableDirectory,
    path.dirname(executableDirectory), path.dirname(path.dirname(executableDirectory)),
  ];
  return candidates.find(isProject) || '';
}

function launchSpec(project) {
  if (!isProject(project)) {
    throw new Error('请选择 MPP 项目目录，目录内应包含 pyproject.toml 和 backend/app/main.py。');
  }
  const python = path.join(project, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!fs.existsSync(python)) {
    throw new Error('项目的 Python 环境尚未准备好。请在项目目录执行 uv sync，再点击重试。');
  }
  if (!fs.existsSync(path.join(project, 'web', 'dist', 'index.html'))) {
    throw new Error('前端尚未构建。请在项目的 web 目录执行 npm ci 和 npm run build，再点击重试。');
  }
  if (!fs.existsSync(path.join(project, 'backend', 'app', 'desktop.py'))) {
    throw new Error('所选项目需要更新桌面启动模块 backend/app/desktop.py。请更新项目后重试。');
  }
  return { command: python, args: ['-m', 'app.desktop'], cwd: path.join(project, 'backend') };
}

module.exports = { argumentValue, discoverProject, isProject, launchSpec };
