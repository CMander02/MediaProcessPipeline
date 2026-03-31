# CLI 改造规划

日期：2026-03-26

## 设计原则

- CLI 本身无状态（或几乎无状态），所有状态从后端数据库实时获取后展示
- 例外：登录态（如将来引入认证）可以持久化到本地
- daemon 未运行时，`list` 和 `config` 支持离线读取 SQLite / settings.json

---

## 当前命令（现状）

### mpp serve

启动 FastAPI daemon。

```
mpp serve [--host HOST] [--port PORT] [--reload]
```

- `--host`：绑定地址，默认 `127.0.0.1`
- `--port`：端口，默认 `18000`
- `--reload`：开发模式热重载

### mpp run

提交任务并 SSE 实时跟踪进度。

```
mpp run <SOURCE> [--no-sep] [--speakers N] [--hotwords WORDS]
```

- `SOURCE`：本地文件路径或 URL
- `--no-sep`：跳过人声分离
- `--speakers`：指定说话人数量（不填自动检测）
- `--hotwords`：热词列表，逗号分隔

行为：提交后打印 task_id，监听 SSE 实时显示进度条，Ctrl+C 不影响后台任务。

### mpp status

查看当前活跃/排队任务总览。

```
mpp status
```

显示统计摘要（总数、处理中、排队中、已完成、已失败）以及活跃任务表格。

### mpp list

查看历史任务列表。

```
mpp list [--status STATUS] [--limit N]
```

- `--status`：按状态过滤（pending/queued/processing/completed/failed/cancelled）
- `--limit`：最多返回条数，默认 20

离线支持：daemon 未运行时直接读 SQLite。

### mpp show

查看单个任务详情。

```
mpp show <TASK_ID>
```

- 支持前缀匹配（不需要完整 ID）
- 显示：ID、状态、源、当前步骤、进度、错误信息、输出目录

### mpp cancel

取消任务。

```
mpp cancel <TASK_ID>
```

- 支持前缀匹配

### mpp config

查看或修改运行时配置。

```
mpp config                  # 列出所有配置
mpp config <KEY>            # 查询单个值
mpp config <KEY> <VALUE>    # 修改值（自动推断 bool/int/float/string）
```

离线支持：daemon 未运行时直接读写 `data/settings.json`。
API key 值展示时掩码（只显示前 8 字符 + `...`）。

---

## 改造方向（待讨论和确认）

### 新增命令

#### mpp attach

挂上一个已在运行的任务，实时看进度（与 `run` 的 SSE 跟踪逻辑相同，但针对已有任务）。

```
mpp attach <TASK_ID>
```

#### mpp retry

对失败或取消的任务，用相同参数重新提交。

```
mpp retry <TASK_ID>
```

#### mpp open

打开任务输出目录（调用系统文件管理器）。

```
mpp open <TASK_ID>
```

### mpp run 新增参数

- `--lang`：指定语言（临时覆盖，不改全局配置）
- `--asr`：临时覆盖 asr_backend
- 提交成功后显示 task_id，方便后续 `attach`/`show`

### mpp list 增强

- `--watch` / `-w`：持续刷新（类似 `watch mpp list`）
- `--format json`：JSON 输出，方便脚本处理
- 默认显示活跃任务，`--all` 显示全部历史

### mpp show 增强

- 显示已生成的输出文件列表及大小
- 显示各步骤耗时

### mpp status 与 mpp list 关系

两者功能有重叠，可考虑：
- 合并：`mpp list` 默认只显示活跃，`--all` 显示历史，去掉 `mpp status`
- 或保留 `mpp status` 只做统计摘要，`mpp list` 专注列表

### mpp config 子命令化

当前用 positional args 区分行为，可改成子命令更清晰：

```
mpp config list
mpp config get <KEY>
mpp config set <KEY> <VALUE>
```

---

## 优先级建议

1. `mpp attach` — 高频需求，run 之后想 re-attach 看进度
2. `mpp run` 新增 `--lang`/`--asr` 临时覆盖
3. `mpp list --watch`
4. `mpp show` 显示输出文件列表
5. `mpp retry`
6. `mpp open`
7. `mpp config` 子命令化
