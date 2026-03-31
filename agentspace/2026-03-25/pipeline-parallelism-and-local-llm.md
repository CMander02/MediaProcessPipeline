# Pipeline 并行化 + 本地 LLM + 任务列表视图

> 2026-03-25 — 构想阶段，暂不执行

## 1. 任务列表视图

**当前状态**：
- files-page 只显示已完成的 archive
- task-card.tsx / task-detail.tsx 存在但没有在主界面展示
- useTasks() hook 已有 SSE 实时更新能力

**建议放置位置**：files-page 顶部，archive 网格上方
- 有活跃/排队任务时显示一个紧凑的任务列表区域
- 每个任务一行：状态图标 + 标题 + 当前步骤 + 进度条 + 队列位置
- 无活跃任务时此区域隐藏，不占空间
- 点击任务行跳转到对应的 result page

**替代方案**：顶部 tab 栏增加 "处理" tab 旁显示角标数字（如 "处理 ②"）

## 2. Pipeline 步骤间并行

**当前**：单 worker，Task #1 全部完成 → Task #2 开始

**提议**：步骤级流水线

```
时间 →  T1      T2      T3      T4      T5      T6
Task#1  下载    分离    转录    分析    润色    归档
Task#2          下载    分离    转录    分析    润色
Task#3                  下载    分离    转录    分析
```

**约束分析**：

| 步骤 | 资源 | 能否与其他步骤并行 |
|------|------|-------------------|
| 下载 | CPU/网络 | 可以，不占 GPU |
| 分离 (UVR) | GPU ~8.6GB | 不能和转录同时（共用 GPU） |
| 转录 (ASR) | GPU ~13GB | 不能和分离同时 |
| 分析 (LLM) | GPU 或 API | 如果用 API：可以和任何步骤并行。如果本地 LLM：不能和 ASR 同时 |
| 润色 (LLM) | GPU 或 API | 同上 |
| 归档 | CPU | 可以，不占 GPU |

**关键洞察**：
- 如果 LLM 用云 API（Anthropic/OpenAI/DeepSeek）：分析/润色不占本地 GPU，可以和 ASR 并行
- 如果 LLM 用本地模型：需要卸载 ASR 后加载 LLM，不能并行
- 下载和归档始终可以并行

**实现方案**：资源感知调度器

```python
class ResourceScheduler:
    gpu_lock = asyncio.Lock()      # GPU 独占
    download_sem = Semaphore(2)     # 最多 2 个并行下载
    llm_api_sem = Semaphore(4)      # API LLM 并发限制

    async def run_step(self, task, step):
        if step in (SEPARATE, TRANSCRIBE):
            async with self.gpu_lock:
                await execute(task, step)
        elif step in (ANALYZE, POLISH) and is_api_llm():
            async with self.llm_api_sem:
                await execute(task, step)
        elif step in (ANALYZE, POLISH) and is_local_llm():
            async with self.gpu_lock:
                await execute(task, step)
        else:  # DOWNLOAD, ARCHIVE
            await execute(task, step)
```

**短期务实方案**：
- 保持单 worker，但在 Task #1 进入 ANALYZE 步骤（如果用 API LLM）时，允许 Task #2 开始 DOWNLOAD
- 不需要完全重写调度器，只需在 worker 中增加一个 "预取" 逻辑

## 3. 本地 LLM 选项

**当前 LLM 架构**：LiteLLM 统一调用，支持 anthropic / openai / custom (OpenAI-compatible API)

**本地模型候选**：

| 模型 | 大小 | 显存需求 | 备注 |
|------|------|---------|------|
| Qwen3.5-4B (满血 BF16) | ~8GB | ~8.5GB | 本地已有，HF 格式，直接用 |
| Qwen3.5-27B Q5_K_M GGUF | ~18GB | ~19GB | 需下载，需 llama-cpp-python |
| Qwen3.5-9B Q8 GGUF | ~9.5GB | ~10GB | 本地已有 |

**VRAM 分析**（24GB 显卡）：
- ASR 模型加载后占 13GB → 剩 11GB
- **不能同时加载 ASR + LLM**（即使 4B 也要 8.5GB，总计 21.5GB 接近极限）
- **解决**：pipeline 串行，ASR 步骤完成后卸载 ASR 模型释放 VRAM，再加载 LLM

**实现路径 A：vLLM 本地服务（推荐）**
- 启动 vLLM 作为独立进程，暴露 OpenAI-compatible API
- `vllm serve Qwen3.5-4B --port 8001 --gpu-memory-utilization 0.4`
- settings 里设 `llm_provider: "custom"`, `custom_api_base: "http://localhost:8001/v1"`
- 优点：已有 custom provider 支持，无需改代码
- 缺点：需要手动管理 vLLM 进程，VRAM 与 ASR 冲突

**实现路径 B：pipeline 内动态加载/卸载（更集成）**
- 新增 `local` LLM provider
- ASR 步骤结束后 `torch.cuda.empty_cache()` + 卸载 ASR 模型
- 加载本地 LLM（transformers + BF16）
- LLM 步骤结束后卸载 LLM，下个任务重新加载 ASR
- 需要改动：`llm.py` 增加 local provider，`pipeline.py` 增加模型卸载逻辑
- `settings.py` 新增：`local_model_path`, `local_model_name`

**实现路径 C：llama-cpp-python（GGUF 专用）**
- 只用于 GGUF 格式模型
- 可以限制 GPU layers 来控制 VRAM 使用
- `custom_api_base` 指向 llama-cpp-python 的 server

**推荐**：路径 B（动态加载），用 `Qwen3.5-4B` 满血模型
- 本地已有模型文件
- 8.5GB 完全够用（ASR 卸载后有 24GB 空间）
- HF transformers 原生支持，不需要额外依赖
- 4B 模型推理速度快，适合批量 polish 调用

**settings.py 新增字段**：
```python
# Local LLM
local_model_path: str = ""           # e.g., "C:/zychen/AIGC/Models/Qwen3.5-4B"
local_device: str = "cuda"
local_dtype: str = "bfloat16"        # bfloat16 / float16 / auto
local_max_new_tokens: int = 4096
```

**llm_provider 新增选项**：`"local"` — 直接用 transformers 推理，不走 LiteLLM

## 涉及文件

| 文件 | 变更 |
|------|------|
| `backend/app/core/settings.py` | 新增 local LLM 配置字段 |
| `backend/app/services/analysis/llm.py` | 新增 local provider 分支 |
| `backend/app/core/pipeline.py` | ASR 卸载 + LLM 加载/卸载逻辑 |
| `backend/app/core/queue.py` | 可选：资源感知调度 |
| `web/src/components/pages/files-page.tsx` | 任务列表区域 |
| `web/src/components/task-card.tsx` | 复用/调整 |
| `web/src/hooks/use-tasks.ts` | 复用 |

## 实施优先级

1. **本地 LLM（Qwen3.5-4B）**— 收益高，改动集中在 llm.py + settings.py
2. **任务列表视图** — 前端改动，复用现有组件
3. **Pipeline 并行** — 架构改动大，短期收益有限（瓶颈在 ASR）
