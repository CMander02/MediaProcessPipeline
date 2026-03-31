# 任务队列增强构想

> 2026-03-25 — 构想阶段，暂不执行

## 需求

1. **批量提交** — 连续上传多个视频/URL，按提交顺序排队处理
2. **逐任务参数配置** — 前端为每个视频单独配置 num_speakers、hotwords 等
3. **CLI 队列可视化** — `mpp status` 显示队列位置和每个任务的执行进度
4. **CLI 离线提交** — daemon 不在线时也能提交任务，daemon 启动后自动执行

## 当前架构

- 单 worker asyncio.Queue（GPU 瓶颈，逐个执行）— 已存在
- 任务持久化 SQLite（WAL 模式）— 已存在
- `TaskQueue.start()` 启动时恢复 QUEUED/PROCESSING 任务 — 已存在
- CLI 的 `mpp list` 已有离线读 SQLite 的先例

## 设计方案

### 后端

- `POST /api/tasks/batch` — 批量创建端点，接收 `items: TaskCreate[]`
- `GET /api/tasks/queue` — 队列快照端点，返回 position + task 列表
- 重构 `create_task` 路由，抽取 `_create_and_enqueue_task()` 共用

### 前端

渐进式体验：
- 单项时不变
- 多项时显示 staging list，每项可展开配置参数
- 全局默认配置 + 逐项覆盖
- 批量提交后导航到 files 页面

### CLI

- `mpp run` 支持多源：`mpp run url1 url2 file1`
- `mpp run` 离线模式：直接写 SQLite，daemon 启动后自动拾取
- `mpp status` 显示带位置编号的队列表（在线/离线两种路径）

### 待定：前端离线能力

用户提出前端也应能在后端不启动时工作（直接读 SQLite + 调 CLI）。
分析：浏览器环境不可行（沙箱隔离），Electron 模式下可通过 IPC 实现。
可选方案：
1. Electron IPC 层：main process 读 SQLite + spawn CLI，前端通过 IPC 调用
2. 轻量 daemon 自动启动：CLI/Electron 首次操作时自动拉起后端
3. 保持现状：前端始终通过 HTTP API，离线能力只在 CLI

**此项暂未决定，需进一步讨论。**

## 涉及文件

| 文件 | 变更 |
|------|------|
| `backend/app/models/task.py` | 新增 `BatchTaskCreate` |
| `backend/app/api/routes/tasks.py` | 新增 batch + queue 端点 |
| `backend/app/cli/main.py` | run 多源 + 离线 + status 增强 |
| `backend/app/cli/client.py` | 新增 `get_queue()`, `create_tasks_batch()` |
| `backend/app/cli/display.py` | 新增 `print_queue_table()` |
| `web/src/components/pages/submit-page.tsx` | 多项目 staging list 重写 |
| `web/src/lib/api.ts` | 新增 `tasks.createBatch()` |

## 实施顺序

1. 后端 batch + queue 端点
2. CLI 离线提交 + 队列可视化 + 多源
3. 前端 submit page 重写
