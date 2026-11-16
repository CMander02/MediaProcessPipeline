# MediaProcessPipeline

媒体处理管线 - 将音视频转化为结构化知识。

## 功能特性

- **媒体下载**: 支持 YouTube、Bilibili 等平台视频下载 (yt-dlp)
- **本地文件处理**: 支持直接处理本地音视频文件
- **平台字幕优先**: 自动下载平台字幕，LLM 补充说话人标注和标点
- **人声分离**: UVR5 (audio-separator) 分离人声和背景音乐
- **语音转录**: sherpa-onnx 统一运行 Qwen3-ASR、SenseVoice、Paraformer、Whisper ONNX，并保留 SiliconFlow API 与 MOSS 本地链路
- **智能润色**: LLM 滑动窗口润色，修正错字、添加标点
- **内容分析**: 自动提取关键信息、生成摘要和思维导图（支持 map-reduce 长文本）
- **统一 Web/PWA/Android**: 同一 React 界面服务本机 PC、服务器浏览器和 Capacitor Android
- **Electron 桌面入口**: 双击启动、自动连接或启动后端、最小化后台处理，复用现有 Python 环境和模型
- **响应式体验**: PC/Pad 使用顶部导航，手机使用底部导航与触控布局

## 项目结构

```
MediaProcessPipeline/
├── backend/                    # Python 后端 (FastAPI :18000)
│   ├── app/
│   │   ├── main.py             # 入口，同时 serve 前端静态文件
│   │   ├── core/               # settings, database, events, queue, pipeline
│   │   ├── models/             # 数据模型
│   │   ├── api/routes/         # API 路由（薄 wrapper）
│   │   └── services/           # 业务逻辑
│   │       ├── ingestion/      # yt-dlp 下载
│   │       ├── preprocessing/  # UVR5 人声分离, VAD 切分
│   │       ├── recognition/    # sherpa-onnx 统一 ASR 与说话人分离
│   │       ├── analysis/       # LLM 润色/摘要/思维导图
│   │       └── archiving/      # 结果归档
│   └── run.py
├── web/                        # Vite + React 19 + shadcn/ui
│   ├── src/                    # PC、服务器与 Android 的唯一 UI 源码
│   └── android/                # Capacitor Android 轻量原生外壳
├── desktop/                    # Electron 桌面入口和 Windows 安装包
├── scripts/                    # CLI 快捷脚本
│   ├── mpp.ps1                 # PowerShell
│   ├── start-web.ps1           # Windows Web 一键启动
│   ├── start-web.sh            # Linux Web 一键启动
│   ├── android.ps1             # Android 检查、构建与调试
│   └── mpp                     # bash
└── config.json                 # 运行时配置；
                                # 实际数据/任务输出/SQLite 走 settings.data_root
                                # 默认使用当前用户的系统应用数据目录
```

## 快速开始

### 环境要求

- Python 3.11 ~ 3.12
- [uv](https://docs.astral.sh/uv/) (Python 包管理)
- Node.js 22.12+
- FFmpeg (必须在 PATH 中)
- CUDA (可选，用于 sherpa-onnx ASR / UVR / HF 本地推理)

### 安装

```bash
git clone <repo-url>
cd MediaProcessPipeline

# Python 依赖（包含 sherpa-onnx CPU 运行时）
uv sync

# 可选：API ASR 使用 Silero ONNX VAD 切片
uv sync --extra asr-api-vad

# 可选：Pyannote 与本地 ASR 配套依赖；此命令保留 sherpa-onnx CPU wheel
uv sync --extra local-asr

# Windows + NVIDIA：安装 CUDA 12 / cuDNN 9 wheel
uv pip install "sherpa-onnx==1.13.4+cuda12.cudnn9" -f https://k2-fsa.github.io/sherpa/onnx/cuda.html

# 安装并校验默认的四套 ONNX ASR 模型与 Silero VAD
uv run python scripts/install_sherpa_models.py --model-root C:/Models/sherpa-onnx

# 可选：UVR 人声分离
uv sync --extra uvr

# 可选：HF Transformers 本地推理（torch + accelerate；transformers 在 base 中）
uv sync --extra hf-local-inference

# 可选：完整本地模型链路（sherpa-onnx、UVR、Pyannote、HF local LLM）
uv sync --extra local-models

# Windows 一次完成完整本地依赖、CUDA wheel 和四模型安装
./scripts/setup.ps1 -Extra local-models -InstallSherpaModels -SherpaModelRoot C:/Models/sherpa-onnx

# 前端依赖 + 构建
cd web && npm install && npm run build && cd ..
```

### Linux 服务器配置（2c4g 受限内存算力节点与纯 API 模式）

2 核 4 GB 级别或仅能分配 2 至 3 GiB 可用内存（如 `CPUQuota=200%`、`MemoryMax=3G`、无 Swap）的 Linux 服务器，既可以作为多端协调与归档同步中枢，也可以直接运行轻量化本地 CPU 推理。在无 GPU 机器上启用本地模型前，将各阶段设备显式设为 `cpu` 并收紧并发与窗口参数：

```bash
uv sync
cd backend
uv run python -m app.cli config set llama_asr_device cpu
uv run python -m app.cli config set llama_asr_concurrency 1
uv run python -m app.cli config set sherpa_device cpu
uv run python -m app.cli config set sherpa_num_threads 2
uv run python -m app.cli config set uvr_device cpu
uv run python -m app.cli config set uvr_mdx_segment_size 64
uv run python -m app.cli config set pyannote_device cpu
uv run python -m app.cli config set local_llm_device cpu
```

如果不希望在服务器上加载任何本地语音模型，也可切换为纯云端 API ASR 与固定 FFmpeg 切片：

```bash
uv run python -m app.cli config set asr_provider siliconflow
uv run python -m app.cli config set siliconflow_asr_chunk_strategy ffmpeg
uv run python -m app.cli config set enable_diarization false
uv run python -m app.cli config set enable_voiceprint false
```

Playwright Python 包随 base 安装。小红书、知乎等浏览器抓取需要额外安装浏览器运行时：

```bash
uv run playwright install chromium
```

### 启动

**桌面应用（Windows 与 macOS）**

Windows 双击 `start-desktop.bat`，或执行：

```powershell
.\scripts\start-desktop.ps1
```

macOS（Apple Silicon）可在 `desktop/` 执行 `npm run pack` 生成 `desktop/dist/mac-arm64/MPP Desktop.app`，或执行 `npm run dist` 生成 DMG。Windows 免安装单文件版在 `desktop/` 执行 `npm run portable`，生成仓库根目录下的 `MPP.exe`。安装、打包、日志与进程管理说明见 [MPP 桌面应用](docs/desktop.md)。

**方式 1：本机 Web**

```powershell
.\start.bat
```

也可以直接运行启动脚本：

```powershell
.\scripts\start-web.ps1
```

健康检查通过后打开 [http://localhost:18000](http://localhost:18000)。

**方式 2：Linux / 服务器 Web**

```bash
./scripts/start-web.sh             # 本机浏览器模式
./scripts/start-web.sh --server --no-browser
```

服务器模式强制 API Token。systemd、Caddy/Nginx 和 HTTPS 配置见 [Web/PWA 启动与部署](docs/web-deployment.md)。

手动启动后端：

```bash
cd backend
uv run python -m app.cli serve     # 启动 daemon :18000
```

浏览器打开 [http://localhost:18000](http://localhost:18000)。

**方式 3：Android App**

Android 复用 `web/src/`，首次打开后填写 MPP 服务器地址和 API Token。构建环境需要 Node.js 22+、Android Studio 2025.2.1+ 和 Android SDK 36。

```powershell
cd web
npm run android:apk
```

Release APK 输出到 `web/android/app/build/outputs/apk/release/`。Debug APK 使用 `npm run android:debug`，应用商店 AAB 使用 `npm run android:aab`。连接、离线同步、ADB 调试、Keystore 和签名说明见 [Capacitor Android 构建与调试](docs/android-capacitor.md)。

**方式 4：CLI**

```bash
# PowerShell
.\scripts\mpp.ps1 server start     # 启动独立后台 daemon
.\scripts\mpp.ps1 run <url-or-file> # 提交并等待
.\scripts\mpp.ps1 submit .\media\ --recursive # 批量提交
.\scripts\mpp.ps1 status           # 任务统计与活跃任务

# bash
./scripts/mpp server start
./scripts/mpp run <url>

# 远程 daemon、Bearer token 与 JSON 输出
./scripts/mpp --server https://mpp.example --token-env MPP_TOKEN --json task list

# 当前进程内的一次性 CLI / API 流程
./scripts/mpp config preset api-flow
./scripts/mpp run --direct --api-flow <url-or-file>
```

CLI 已覆盖任务生命周期、归档与字幕、Provider/模型/flow、来源认证、知识库、声纹、日志、存储、服务端文件系统和移动端同步。完整命令见 [CLI 使用参考](docs/cli.md)。`mpp serve` 保留为前台调试入口。

### 开发模式

```bash
# 后端 (热重载)
cd backend && uv run python run.py --reload

# 前端 (Vite dev server, 代理 API 到 :18000)
cd web && npm run dev              # :5173
```

## API 端点

### 任务管理
- `POST /api/tasks` - 创建处理任务
- `GET /api/tasks` - 列出任务
- `GET /api/tasks/{id}` - 获取任务详情
- `GET /api/tasks/{id}/events` - SSE 实时进度
- `GET /api/tasks/events` - 全局 SSE 事件流
- `POST /api/tasks/{id}/cancel` - 取消任务

### 管道操作
- `POST /api/pipeline/upload` - 上传本地文件
- `POST /api/pipeline/polish` - 润色文本
- `POST /api/pipeline/summarize` - 生成摘要
- `POST /api/pipeline/mindmap` - 生成思维导图
- `GET /api/pipeline/archives` - 列出归档
- `DELETE /api/pipeline/archives` - 删除归档
- `GET /api/sync/changes` - Android 增量同步归档变更
- `GET /api/sync/archives/{id}/manifest` - 获取离线文件清单
- `GET /api/sync/archives/{id}/files/{path}` - 下载清单内的离线文件

### 设置
- `GET /api/settings` - 获取运行时设置
- `PUT /api/settings` - 更新设置

## 处理流程

1. **下载与导入**：从 URL 下载或导入本地音视频与图文，自动校验并优先复用合规的平台字幕。
2. **人声分离**：可选运行 UVR5 分离人声与背景音乐，流式合并限制峰值内存。
3. **语音识别与说话人分离**：支持 `llama.cpp`（Qwen3-ASR GGUF）、`sherpa-onnx`、SiliconFlow API 与 `moss`，可选 Pyannote 说话人聚类与声纹匹配。
4. **文本润色与分析**：LLM 核对说话人姓名、滑动窗口润色字幕、提取结构化分析、生成摘要与思维导图，完成后自动清理中间提取音频。
5. **归档与多端同步**：写入 `data_root/archives/{title}/` 与 SQLite 归档索引，向其他设备增量同步纯文本产物与封面。

## 输出文件

每个任务在资料库的 `archives/{title}/`（旧混合布局为 `data_root/{title}/`）下生成：

- `metadata.json`：媒体与来源元数据、归档 ID
- `analysis.json`：LLM 结构化分析结果
- `transcript.srt`：原始识别字幕
- `transcript_polished.srt` 与 `transcript_polished.md`：润色后字幕与文稿
- `summary.md` 与 `mindmap.md`：结构化摘要与思维导图
- `cover.*`、`images/`、`descriptions/`：封面图与图文关键帧说明
- `media_assets.json`：媒体文件用途记录，供空间回收策略按需清理

## 配置

运行时配置保存在项目根目录 `config.json`。新安装的 `data_root` 默认位于系统用户应用数据目录：macOS 为 `~/Library/Application Support/MediaProcessPipeline`，Windows 为 `%LOCALAPPDATA%/MediaProcessPipeline`，Linux 为 `~/.local/share/MediaProcessPipeline`（支持 `XDG_DATA_HOME`）。设置中可指定其他资料库位置，已有配置保持生效。任务数据库由 `data_root` 和资料库布局决定：旧库使用 `tasks.db`，新库使用 `state/tasks.db`；新归档位于 `archives/<title>/`。可用 `mpp storage migrate` 预览目录整理和资料库复制，详见[资料库迁移与恢复](docs/storage-migration.md)。

在前端设置页面或根目录 `config.json` 中配置：

- **LLM**：Provider（OpenAI、Anthropic、DeepSeek、OpenAI 兼容端点、本地 `llama.cpp` / Transformers、OAuth CLI）与阶段模型绑定
- **ASR 与说话人**：`llama.cpp`、`sherpa-onnx`、SiliconFlow 或 `moss` 模型路径，非手机端均可选 CPU 或 GPU 运行设备
- **UVR**：模型选择、模型目录与 CPU/GPU 运行设备
- **多端协同**：远程服务器地址、API Token、优先执行节点与归档同步策略，详见[多设备协同与计算架构](docs/multi-device-architecture.md)

## 技术栈

- **Backend**: Python 3.11+, FastAPI, SQLite, uv
- **Frontend**: React 19, Vite, shadcn/ui, Tailwind CSS 4
- **Desktop & Mobile**: Electron 桌面端（Windows / macOS）、Capacitor Android 原生离线资料库
- **AI**: llama.cpp (Qwen3-ASR / Local GGUF LLM), sherpa-onnx, pyannote-audio, UVR5 (audio-separator), OpenAI SDK
- **下载与处理**: yt-dlp, Playwright, FFmpeg

## 演进路线与待办

架构目标见 [多设备协同与计算架构](docs/multi-device-architecture.md)，具体待办事项与发版里程碑统一在 [GitHub Issues](https://github.com/CMander02/MediaProcessPipeline/issues) 中跟踪。

## License

MIT
