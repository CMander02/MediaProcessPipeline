# MediaProcessPipeline

媒体处理管线 - 将音视频转化为结构化知识。

## 功能特性

- **媒体下载**: 支持 YouTube、Bilibili 等平台视频下载 (yt-dlp)
- **本地文件处理**: 支持直接处理本地音视频文件
- **平台字幕优先**: 自动下载平台字幕，LLM 补充说话人标注和标点
- **人声分离**: UVR5 (audio-separator) 分离人声和背景音乐
- **语音转录**: WhisperX / Qwen3-ASR，支持说话人分离
- **智能润色**: LLM 滑动窗口润色，修正错字、添加标点
- **内容分析**: 自动提取关键信息、生成摘要和思维导图（支持 map-reduce 长文本）
- **桌面应用**: Electron 打包，双击即用

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
│   │       ├── recognition/    # WhisperX / Qwen3-ASR 转录
│   │       ├── analysis/       # LLM 润色/摘要/思维导图
│   │       └── archiving/      # 结果归档
│   └── run.py
├── web/                        # Vite + React 19 + shadcn/ui
│   └── src/
├── electron/                   # Electron 桌面壳
│   └── main.js
├── scripts/                    # CLI 快捷脚本
│   ├── mpp.ps1                 # PowerShell
│   └── mpp                     # bash
└── data/                       # 数据目录（settings.json, tasks.db）
```

## 快速开始

### 环境要求

- Python 3.11 ~ 3.12
- [uv](https://docs.astral.sh/uv/) (Python 包管理)
- Node.js 18+
- FFmpeg (必须在 PATH 中)
- CUDA (可选，GPU 加速 ASR/UVR)

### 安装

```bash
git clone <repo-url>
cd MediaProcessPipeline

# 后端依赖
cd backend && uv sync && cd ..

# 前端依赖 + 构建
cd web && npm install && npm run build && cd ..
```

### 启动

**方式 1: Electron 桌面应用**

```bash
cd electron && npm install && npm start
```

或打包后双击 `MPP.exe`（放在项目根目录）。

**方式 2: 后端 + 浏览器**

```bash
cd backend
uv run python -m app.cli serve     # 启动 daemon :18000
```

浏览器打开 http://127.0.0.1:18000

**方式 3: CLI**

```bash
# PowerShell
.\scripts\mpp.ps1 serve            # 启动 daemon
.\scripts\mpp.ps1 run <url>        # 提交任务
.\scripts\mpp.ps1 list             # 查看任务列表
.\scripts\mpp.ps1 status           # daemon 状态

# bash
./scripts/mpp serve
./scripts/mpp run <url>
```

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

### 设置
- `GET /api/settings` - 获取运行时设置
- `PUT /api/settings` - 更新设置

## 处理流程

1. **下载/导入** - 从 URL 下载或导入本地文件，自动尝试下载平台字幕
2. **人声分离** - UVR5 分离人声（有平台字幕时跳过）
3. **语音转录** - WhisperX / Qwen3-ASR 转录（有平台字幕时由 LLM 处理）
4. **内容分析** - LLM 提取元数据、关键词、主题
5. **文本润色** - LLM 滑动窗口修正转录错误、生成摘要和思维导图
6. **归档输出** - 保存结构化文件到 `data/{title}/`

## 输出文件

每个任务在 `data/{title}/` 下生成：

- `source/` - 原始媒体文件
- `metadata.json` - 媒体元数据
- `analysis.json` - LLM 分析结果
- `transcript.srt` - 原始转录 SRT
- `transcript_polished.srt` - 润色后 SRT
- `transcript_polished.md` - 干净的 Markdown 文稿
- `summary.md` - 摘要和思维导图

## 配置

在前端 Settings 页面或 `data/settings.json` 中配置：

- **LLM**: Provider (OpenAI / Anthropic / DeepSeek 等 OpenAI 兼容), 模型, API 密钥
- **ASR**: 后端选择 (WhisperX / Qwen3-ASR), 模型大小, 语言, 设备
- **UVR**: 模型选择, 模型目录
- **路径**: 数据目录

## 技术栈

- **Backend**: Python 3.11+, FastAPI, SQLite, uv
- **Frontend**: React 19, Vite, shadcn/ui, Tailwind CSS 4
- **Desktop**: Electron
- **AI**: WhisperX, Qwen3-ASR, UVR5 (audio-separator), LiteLLM
- **下载**: yt-dlp, FFmpeg

## 已知问题 / 待办

- **导图节点聚焦交互未完全解决**: 当前已实现“点击节点后按局部子树重新取景”的一版近似方案，但对于右侧长文本节点，镜头中心、缩放范围、以及“局部节点先布局再把其他节点拼接回来”的效果仍不稳定，和预期交互还有差距。
- **LLM API 总结链路需要切换到本地模型**: 目前总结/导图相关能力仍主要依赖远程 LLM API，后续需要支持使用本地模型完成总结与知识树生成。
- **知识树总结会误把多个章节并在一起**: 典型案例是“翁家翌”相关内容，多个章节会在知识树摘要阶段被合并，导致章节边界和主题拆分不准确。

## License

MIT
