# MediaProcessPipeline - Dev Spec

媒体处理管线 - 将音视频转化为结构化知识。

## 架构

```
CLI (mpp) / Gradio (/ui) / HTTP
        ↓
  FastAPI Daemon (:18000)
  ├─ TaskQueue    asyncio.Queue, 单 worker (GPU 瓶颈)
  ├─ TaskStore    SQLite (data/tasks.db)
  ├─ EventBus    in-process pub/sub → SSE
  └─ Services    ASR, UVR, LLM (不变)
```

## 开发规范

### 后端 (Python / FastAPI)

- 端口固定 **18000**
- 包管理用 **uv**
- 启动 daemon: `cd backend && uv run python -m app.cli serve` 或 `uv run python run.py --reload`
- Windows 上必须用 UTF-8 编码处理文件路径（emoji 文件名）
- 所有 service 使用 singleton 模式，通过 `get_xxx_service()` 获取
- 核心模块在 `app.core/`: settings, database, events, queue, pipeline
- Runtime settings 定义在 `app.core.settings`，API route 是薄 wrapper
- LLM 调用统一走 LiteLLM，支持 anthropic / openai / custom (OpenAI compatible)
- ASR 后端支持 WhisperX 和 Qwen3-ASR，通过 runtime settings 切换
- UVR5 人声分离保留——工作场景多样，不止访谈，可能有背景音乐等复杂音频

### CLI (`mpp`)

- 入口: `cd backend && uv run python -m app.cli <command>`
- 快捷脚本: `scripts/mpp.ps1` (PowerShell) / `scripts/mpp` (bash)
- 命令: `serve`, `run <source>`, `status`, `list`, `show <id>`, `cancel <id>`, `config [key] [value]`
- daemon 未运行时 `list` 和 `config` 可离线读 SQLite / settings.json

### 前端 (Gradio)

- Gradio UI 在 `app/ui/app.py`，mount 在 `/ui`
- `mpp serve` 时通过 `mount_gradio_ui(app)` 挂载，`--reload` 模式下不加载 Gradio
- 4 个 Tab: 处理（提交+活跃队列）、历史、结果查看、设置
- Gradio 直接调用 core 层（不走 HTTP），共享 TaskStore / EventBus / Queue
- 旧 Vue 前端在 `frontend/` 目录，已弃用

### 通信协议

- SSE (Server-Sent Events): `GET /api/tasks/events` (全局) 和 `GET /api/tasks/{id}/events` (单任务)
- 旧轮询方式仍兼容 (`GET /api/tasks/{id}`)

### 数据持久化

- 任务存 SQLite `data/tasks.db`（active + history 统一存储）
- Settings 存 `data/settings.json`
- 任务产出存 `data/{task_id_short}_{title}/`

## 注意事项

- 后端端口 **18000** 不是 8000
- UVR 模型路径：用户可能本地已安装 UVR，可以自动扫描常见安装路径
- ffmpeg 必须在 PATH 中
- CUDA 显存管理：切换 ASR 后端时需要释放旧模型显存
- `ccworkspace/` 目录存放开发过程中的分析和规划文档
