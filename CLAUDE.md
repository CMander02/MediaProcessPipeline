# MediaProcessPipeline - Dev Spec

媒体处理管线 - 将音视频转化为结构化知识。

## 架构

```
Electron (MPP.exe) / CLI (mpp) / HTTP
        ↓
  FastAPI Daemon (:18000)  ← 同时 serve 前端静态文件
  ├─ TaskQueue    asyncio.Queue, 单 worker (GPU 瓶颈)
  ├─ TaskStore    SQLite (data/tasks.db)
  ├─ EventBus    in-process pub/sub → SSE
  └─ Services    ASR, UVR, LLM (不变)

  Frontend: Vite + React 19 + shadcn/ui (web/)
  Desktop:  Electron portable exe (electron/)
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


### 前端 (Vite + React)

- 源码在 `web/`，构建产物在 `web/dist/`
- 后端通过 FastAPI 静态文件 serve `web/dist/`
- **修改前端代码后必须 `cd web && npm run build`**，否则改动不会生效
- 开发时也可用 `npm run dev` 启动 Vite dev server（端口 5173），但生产始终用 build 产物

### 通信协议

- SSE (Server-Sent Events): `GET /api/tasks/events` (全局) 和 `GET /api/tasks/{id}/events` (单任务)
- 旧轮询方式仍兼容 (`GET /api/tasks/{id}`)

### 数据持久化

- 任务存 SQLite `data/tasks.db`（active + history 统一存储）
- Settings 存 `data/settings.json`
- 任务产出存 `data/{title}/`（同名加 `(2)` 后缀）

## Git 提交规范

- **提交前询问用户是否需要变更版本号**
- 版本号位置: `pyproject.toml` + `electron/package.json`
- SemVer: PATCH=bug fix, MINOR=新功能, MAJOR=破坏性变更 / 产品 ready
- 当前阶段 0.x，到知识库 + Agent API 完成后考虑 1.0

## 注意事项

- 后端端口 **18000** 不是 8000
- UVR 模型路径：用户可能本地已安装 UVR，可以自动扫描常见安装路径
- ffmpeg 必须在 PATH 中
- CUDA 显存管理：切换 ASR 后端时需要释放旧模型显存
- `agentspace/` 目录存放开发过程中的分析和规划文档
