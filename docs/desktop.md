# MPP 桌面应用

MPP Desktop 使用 Electron 打开现有 React 界面。FastAPI 继续在 `http://localhost:18000` 提供前端、API、SSE 和媒体处理服务。

## 安装与使用

### 本机免安装使用

在项目的 `desktop/` 目录执行 `npm run portable`，生成 `desktop/dist/MPP-Desktop-Portable.exe`。直接双击即可启动，也可为它创建桌面快捷方式。

单文件 EXE 放在项目根目录或默认的 `desktop/dist/` 时，会自动发现项目。放到其他位置时，首次选择一次项目目录；已选择的目录会持续保存。便携版启动时将 Electron 解压到临时目录，首次打开会有短暂等待。

### 使用安装器

1. 准备已有 MPP 项目：按照 [README 环境安装](../README.md#安装) 配置 uv、Python、FFmpeg 和所需模型，并在 `web/` 执行 `npm ci`、`npm run build`。
2. 运行 `desktop/dist/MPP-Desktop-<版本>-Setup.exe`。安装器可选择安装目录，并创建桌面与开始菜单快捷方式。
3. 打开 **MPP Desktop**。首次使用点击“选择项目目录”，选中包含 `pyproject.toml` 和 `backend/` 的 MPP 根目录。应用会保存选择。
4. 启动完成后进入文件页，沿用 Web 版的处理、任务、设置及文件功能。

安装包包含 Electron 和桌面启动页。Python 从所选项目的 `.venv/` 运行，模型、`config.json`、资料库和日志继续使用原有后端配置。环境由 uv 管理，启动时直接使用已配置的解释器，保留已安装的 CUDA wheel 和可选依赖。

当已有 MPP daemon 在线时，桌面应用会直接连接；保存的项目目录用于服务离线后的自动启动。项目移动后可在启动页或“桌面”菜单重新选择目录。

## 后续升级与配置

- **更新业务前端**：更新项目源码后，在 `web/` 执行 `npm run build`，刷新桌面页面即可加载新界面。前端依赖变化时先执行 `npm ci`。
- **更新 Python 后端**：关闭桌面窗口并退出自己管理的后端，更新项目代码后重新打开 EXE。依赖变化时按项目的 uv 安装说明更新环境。
- **更新桌面窗口或启动逻辑**：退出桌面应用，在 `desktop/` 执行 `npm run portable`，继续双击生成的同名 EXE。桌面 npm 依赖变化时先执行 `npm ci`。
- **项目配置与模型**：`config.json`、`.venv`、模型和资料库继续位于原有项目或配置指定的位置。替换桌面 EXE 会保留它们。
- **项目目录选择与登录状态**：存于 `%APPDATA%/MPP Desktop/`，替换 EXE 后继续使用。项目搬家时，通过“选择项目目录”更新路径。

本机便携版复用这些已配置的路径。将 EXE 移到另一台电脑后，需在那台电脑准备后端项目和环境。

## 窗口与退出

- **顶部菜单栏**：默认隐藏，单按 `Alt` 显示菜单栏。
- **关闭窗口**：退出桌面应用，并停止由本次桌面会话启动的后端。
- **最小化窗口**：保留任务栏入口，后端继续处理任务；再次启动应用会恢复现有窗口。
- **在浏览器中打开**：打开 `http://localhost:18000`，可与桌面窗口同时使用。
- **打开启动日志目录**：查看本次 `desktop.log` 和上次 `desktop.previous.log`，包括 Python 启动输出。
- **重新连接**：重新检查本机后端并加载页面。
- **退出并停止后端**：结束由本次桌面会话启动的后端。应用先请求正常关闭；后端释放队列和模型，超时后清理所属进程树。需要继续处理任务时使用最小化窗口。
- **退出 MPP 桌面**：连接独立 CLI/Web daemon 时显示此项，退出后该 daemon 继续运行。

桌面应用意外结束时，自己启动的 Python 后端通过父进程管道关闭信号执行清理。Windows Job Object 负责回收该后端的 ffmpeg、模型服务等子进程。

## 从源码启动

Windows 需要 Node.js **22.12+**。双击项目根目录的 `start-desktop.bat`，或者执行：

```powershell
.\scripts\start-desktop.ps1

# 修改前端后，重新构建并启动
.\scripts\start-desktop.ps1 -Build
```

脚本首次安装桌面 npm 依赖，缺少前端产物时会构建前端，然后启动独立桌面窗口。`start.bat` 继续用于 Web 浏览器入口。

开发时也可在终端保持 Electron 输出：

```powershell
cd desktop
npm ci
npm start
```

源码启动自动定位仓库。可显式指定项目目录：

```powershell
npm start -- --project "D:\Projects\MediaProcessPipeline"

# 安装后的可执行程序也支持相同参数
& "C:\Path\MPP Desktop.exe" --project "D:\Projects\MediaProcessPipeline"
```

项目选择顺序：`--project` 参数、`MPP_PROJECT_DIR` 环境变量、已保存目录、源码或程序附近的 MPP 项目。更新到包含 `backend/app/desktop.py` 的项目版本后即可自动启动；旧版已在线服务也支持连接。

## 构建 Windows 安装包

先在 `web/` 安装依赖，再执行：

```powershell
cd desktop
npm ci
npm run dist
```

构建会先执行 `web` 的 `npm run build`。桌面发布版本由根目录 `pyproject.toml` 读取。

产物：

- `desktop/dist/MPP-Desktop-<版本>-Setup.exe`：Windows x64 安装器。
- `desktop/dist/MPP-Desktop-Portable.exe`：运行 `npm run portable` 生成的免安装单文件程序。
- `desktop/dist/win-unpacked/MPP Desktop.exe`：可直接运行的应用；移动时需保留整个 `win-unpacked/` 目录。

只构建应用目录可执行 `npm run pack`。发布签名沿用 electron-builder 的签名配置；本机开发打包可生成未签名安装器。Python/CUDA/模型的首次自动安装和应用自动更新属于后续功能。

## 故障处理

| 启动提示 | 处理方式 |
| --- | --- |
| 请选择 MPP 项目目录 | 选中包含 `pyproject.toml`、`backend/app/main.py` 的项目根目录 |
| Python 环境尚未准备好 | 在项目根目录执行 `uv sync`，按需安装模型依赖，再点击“重试” |
| 前端尚未构建 | 在 `web/` 执行 `npm ci` 和 `npm run build` |
| 后端已连接，前端页面尚未就绪 | 构建前端并重启提供该页面的后端，再重试 |
| 端口被其他服务使用 | 释放端口 `18000` 后重试 |
| 服务暂时没有响应 | 等待现有服务完成初始化，或查看该服务日志 |
| 后端启动超过 120 秒 | 查看启动日志、模型路径和模型配置，处理错误后重试 |

如果后端配置了 API Token，桌面页面使用与 Web 版相同的解锁界面。桌面壳仅管理本机服务，外部 HTTP/HTTPS 链接交给默认浏览器打开。

## 开发验证

```powershell
cd desktop
npm test
npm run test:smoke

# 检查构建后的真实应用
node scripts/smoke.cjs --packaged "dist/win-unpacked/MPP Desktop.exe"
node scripts/portable-smoke.cjs

cd ..
uv run --no-sync python -m pytest tests/test_desktop_lifecycle.py -q
```

Windows 冒烟测试要求 `18000` 空闲，复用项目 `.venv` 和构建好的前端，在 `output/desktop-smoke/` 下创建独立资料库，并保存截图、启动日志与检查结果。原有服务在线时测试会停止，便于保留当前任务。测试包括真实 FastAPI/React、API/SSE、目录选择结果、错误重试、最小化与关闭窗口退出、单实例、退出与 CLI 服务归属。自动化目录选择验证通过替换原生对话框的返回值完成，其余运行流程使用实际应用。

实现参考：[Electron 生命周期](https://www.electronjs.org/docs/latest/api/app)、[安全指南](https://www.electronjs.org/docs/latest/tutorial/security)、[electron-builder 配置](https://www.electron.build/docs/configuration/)。
