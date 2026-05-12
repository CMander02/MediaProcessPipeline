# MediaProcessPipeline 执行流程详解

## 1. 输入方式

系统支持三种输入方式：

### 1.1 URL 输入（在线媒体）
- **YouTube**: `https://youtube.com/watch?v=xxx` 或 `https://youtu.be/xxx`
- **Bilibili**: `https://bilibili.com/video/xxx` 或 `https://b23.tv/xxx`
- **其他 yt-dlp 支持的网站**

### 1.2 本地文件路径
- **视频格式**: `.mp4`, `.mkv`, `.avi`, `.webm`, `.mov`
- **音频格式**: `.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg`
- 支持 Windows 绝对路径：`C:\path\to\file.mp4`
- 支持带引号的路径：`"C:\path with spaces\file.mp4"`

### 1.3 文件上传
- 通过 `POST /api/pipeline/upload` 端点上传本地文件
- 文件直接保存到本任务目录 `{data_root}/{title}/`

---

## 2. 完整管线执行流程

当创建类型为 `pipeline` 的任务时，系统按以下步骤顺序执行：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Pipeline 执行流程                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ① DOWNLOAD (下载/复制)                                                     │
│     │                                                                       │
│     ├─ URL输入 → yt-dlp 下载 → 提取音频 (.wav)                              │
│     │         → 提取元数据 (标题/作者/简介/标签/章节)                        │
│     │                                                                       │
│     └─ 本地文件 → 复制到任务目录                                             │
│              ├─ 视频文件 → ffmpeg 提取音频                                   │
│              └─ 音频文件 → 直接使用                                          │
│                                                                             │
│  ② SEPARATE (人声分离)                                                       │
│     │                                                                       │
│     ├─ skip_separation=false → UVR5 分离人声 → vocals.wav                   │
│     │                                                                       │
│     └─ skip_separation=true → 跳过，直接使用原音频                           │
│                                                                             │
│  ③ TRANSCRIBE (语音转录)                                                     │
│     │                                                                       │
│     ├─ 音频时长 ≤ 30分钟 → 直接转录                                          │
│     │                                                                       │
│     └─ 音频时长 > 30分钟 → VAD 静音点分片 → 分段转录 → 合并 SRT              │
│                                                                             │
│  ④ VOICEPRINT (声纹识别)                                                    │
│     │                                                                       │
│     └─ 基于 diarization 结果提取声纹并匹配人物库                             │
│                                                                             │
│  ⑤ POLISH (字幕润色) - 并行处理                                              │
│     │                                                                       │
│     └─ LLM 并行润色 (64段/chunk, 16段重叠, 最多8并发)                        │
│           → 修正错误/添加标点/移除填充词                                     │
│                                                                             │
│  ⑥ ANALYZE (分析+摘要+脑图)                                                  │
│     │                                                                       │
│     ├─ LLM 分析语言/类型/话题/关键词/专有名词                                │
│     ├─ LLM 生成 TLDR/关键要点/待办事项                                       │
│     └─ LLM 生成思维导图 (限制3层深度)                                        │
│                                                                             │
│  ⑦ ARCHIVE (归档保存)                                                        │
│     │                                                                       │
│     └─ 保存所有产出文件到 data/{task_id}_{title}/                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 各阶段详细说明

### 3.1 Step 1: DOWNLOAD (下载媒体)

**代码位置**: `backend/app/api/routes/tasks.py:317-389`

#### URL 输入处理
```python
# 使用 yt-dlp 下载
ingest = await download_media(source)
audio_path = ingest.get("file_path")
metadata = MediaMetadata(**ingest.get("metadata", {}))
```

**yt-dlp 配置** (`backend/app/services/ingestion/ytdlp.py`):
- 格式选择: `bestaudio/best`
- 输出格式: WAV (通过 FFmpeg 后处理)
- 采样率: 192kbps

#### 提取的元数据

| 字段 | 来源 | 说明 |
|------|------|------|
| `title` | yt-dlp | 视频标题 |
| `uploader` | yt-dlp | 作者/频道名 |
| `description` | yt-dlp | 视频简介 (截取前5000字符) |
| `tags` | yt-dlp | 标签列表 + 分类 |
| `chapters` | yt-dlp | 章节标记 (标题+时间戳) |
| `duration_seconds` | yt-dlp | 时长 |
| `upload_date` | yt-dlp | 上传日期 |

#### 本地文件处理
```python
# 视频文件 → 提取音频
if source_path.suffix.lower() in video_exts:
    audio_path = task_dir / f"{title}.wav"
    _extract_audio_from_video(dest_source, audio_path)

# 音频文件 → 直接使用
elif source_path.suffix.lower() in audio_exts:
    audio_path = str(dest_source)
```

**ffmpeg 提取音频参数**:
```
-vn              # 无视频
-acodec pcm_s16le # 16位PCM
-ar 16000        # 16kHz采样率
-ac 1            # 单声道
```

---

### 3.2 Step 2: SEPARATE (人声分离)

**代码位置**: `backend/app/services/preprocessing/uvr.py`

#### 可跳过选项
```python
skip_separation = task.options.get("skip_separation", False)
if skip_separation:
    vocals_path = audio_path  # 跳过分离
else:
    preprocess = await separate_vocals(audio_path, output_dir=task_dir)
    vocals_path = preprocess.get("vocals_path", audio_path)
```

#### UVR5 模型配置
| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `uvr_model` | `UVR-MDX-NET-Inst_HQ_3` | 主模型 |
| `uvr_model_dir` | 自动检测 | 模型目录 |
| 输出格式 | WAV | 固定 |

**支持的模型**:
- `UVR-MDX-NET-Inst_HQ_3` (默认, MDX-Net)
- `1_HP-UVR` (VR 架构)
- `UVR-DeNoise-Lite`
- `Kim_Vocal_2`
- `UVR-DeEcho-DeReverb`
- `htdemucs` (Demucs)

**注意**: 分离后自动删除 instrumental 文件，只保留 vocals。

---

### 3.3 Step 3: TRANSCRIBE (语音转录)

**代码位置**:
- Provider 入口: `backend/app/services/recognition/__init__.py`
- 当前实现: `backend/app/services/recognition/qwen3_asr.py`

#### ASR Provider
| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `asr_provider` | `qwen3` | ASR provider，目前只支持 `qwen3` |

#### Qwen3-ASR 配置
| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `qwen3_asr_model_path` | 空 | 本地模型路径；空值使用 `Qwen/Qwen3-ASR-1.7B` |
| `qwen3_aligner_model_path` | 空 | Qwen ForcedAligner 路径；配置后启用更稳定的时间戳 |
| `qwen3_enable_timestamps` | `true` | 是否请求时间戳 |
| `qwen3_batch_size` | `32` | Qwen3-ASR 推理批次大小 |
| `qwen3_max_new_tokens` | `4096` | 单次生成 token 上限 |
| `qwen3_device` | `cuda` | 计算设备 |
| `enable_diarization` | `true` | 启用说话人分离 |

#### 转录路径

Qwen3-ASR 通过官方 `qwen-asr` 包加载：

```python
from qwen_asr import Qwen3ASRModel
model = Qwen3ASRModel.from_pretrained(model_path, **model_kwargs)
```

有 `qwen3_aligner_model_path` 时，使用 Qwen3-ASR 原生转录和 ForcedAligner
时间戳。没有 ForcedAligner 时，为了保留分段时间，当前实现会用 Silero VAD
切分语音片段，再逐片段调用 Qwen3-ASR。

#### 说话人分离 (Diarization)
```python
if diarize and rt.enable_diarization:
    if rt.pyannote_model_path:
        self._diarize_model = self._load_diarization_model(...)
```

Voiceprint 通过 recognition provider 暴露的 diarization cache hook 复用最近一次
pyannote 结果，不直接依赖具体 ASR 类。

---

### 3.4 Step 4: VOICEPRINT (声纹识别)

如果启用声纹功能且 ASR 产生了说话人标签，系统会在清理 vocals/segments 临时音频前提取各说话人的 embedding，并尝试匹配本地声纹库。匹配结果会回写到转录分段与 SRT，供后续润色、归档和前端展示使用。

平台字幕 fast path 没有 diarization 音频片段，因此该步骤会标记为完成但实际跳过。

---

### 3.5 Step 5: POLISH (字幕润色) - 并行处理

**代码位置**: `backend/app/services/analysis/llm.py`
**Prompt 位置**: `backend/app/services/analysis/prompts/polish.py`

#### 并行分块润色

**第二阶段 LLM 调用** - 使用上下文信息并行润色字幕：

```
┌────────────────────────────────────────────────────────────────┐
│                    并行分块润色机制                             │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  参数配置:                                                      │
│  ├─ chunk_size = 64 段 (每块处理的字幕条数)                     │
│  ├─ overlap = 16 段 (块之间的重叠区域)                          │
│  └─ max_concurrency = 8 (最大并行 LLM 调用数)                   │
│                                                                │
│  处理流程 (1000 段字幕示例):                                    │
│                                                                │
│  段落: [1-64] [49-112] [97-160] ... (共约 21 块)               │
│                                                                │
│  并行执行:                                                      │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┬───────┐   │
│  │ chunk 0 │ chunk 1 │ chunk 2 │ chunk 3 │ chunk 4 │  ...  │   │
│  └─────────┴─────────┴─────────┴─────────┴─────────┴───────┘   │
│       ↓         ↓         ↓         ↓         ↓                │
│  [Semaphore 限制最多 8 个并发]                                  │
│                                                                │
│  合并策略:                                                      │
│  ├─ chunk 0: 保留 1-64 全部                                    │
│  ├─ chunk 1: 跳过前 16 段，保留 17-64 (即原始 65-112)          │
│  └─ 依次类推...                                                 │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**润色 Prompt 使用分析结果作为上下文**:
```python
prompt = get_polish_prompt(
    text=chunk_srt,
    language=context.get("language"),
    content_type=context.get("content_type"),
    main_topics=context.get("main_topics"),
    keywords=context.get("keywords"),
    proper_nouns=context.get("proper_nouns"),
)
```

---

### 3.6 Step 6: ANALYZE (分析+摘要+脑图)

**代码位置**: `backend/app/services/analysis/llm.py`
**Prompt 位置**:
- `backend/app/services/analysis/prompts/analyze.py`
- `backend/app/services/analysis/prompts/summarize.py`
- `backend/app/services/analysis/prompts/mindmap.py`

该步骤先运行内容分析，再基于润色后的文本并行生成摘要和脑图：
```python
analysis = await analyze_content(transcript, metadata.title, metadata=video_metadata)
summary = await summarize_text(transcript)
mindmap = await generate_mindmap(transcript)
```

内容分析输入包括转录文本、视频标题、作者名、简介、标签和章节标记，输出 `language`、`content_type`、`main_topics`、`keywords`、`proper_nouns`、`speakers_detected`、`tone` 等结构化字段。

**摘要输出格式**:
```json
{
    "tldr": "一句话总结（不超过100字）",
    "key_facts": ["关键要点1", "关键要点2", "..."],
    "action_items": ["待办事项1", "..."],
    "topics": ["主题1", "主题2"]
}
```

**思维导图格式** (Markmap, 限制3层深度):
```markdown
- 根节点：全文概括（不超过30字）
  - 一级主题1
    - 二级要点1.1
      - 三级细节1.1.1
      - 三级细节1.1.2
    - 二级要点1.2
  - 一级主题2
    - 二级要点2.1
  - 一级主题3
```

**思维导图约束**:
- 根节点: 全文一句话概括
- 最大深度: 3层 (根 → 一级 → 二级 → 三级)
- 一级主题: 3-6 个
- 每个节点文字: 不超过 20 字

---

### 3.7 Step 7: ARCHIVE (归档保存)

**代码位置**: `backend/app/services/archiving/archive.py`

#### 输出目录结构
```
data/{task_id_short}_{title}/
├── source/                      # 原始文件
│   └── {original_filename}
├── metadata.json                # 媒体元信息 (含 description, tags, chapters)
├── analysis.json                # LLM 内容分析结果
├── summary.json                 # 结构化摘要，供断点恢复使用
├── transcript.srt               # 原始转录 SRT
├── transcript_polished.srt      # 润色后 SRT
├── transcript_polished.md       # 润色后 Markdown
├── summary.md                   # 渲染后的摘要
└── mindmap.md                   # Markmap 思维导图
```

#### Obsidian 同步
如果配置了 `obsidian_vault_path`，会自动将 `.md` 文件复制到：
```
{obsidian_vault_path}/MediaPipeline/{output_dir_name}/
```

---

## 4. Prompt 配置

所有 LLM Prompt 模板位于 `backend/app/services/analysis/prompts/` 目录：

```
prompts/
├── __init__.py      # 导出所有 prompt 函数
├── analyze.py       # 内容分析 prompt (get_analyze_prompt)
├── polish.py        # 润色 prompt (get_polish_prompt, get_simple_polish_prompt)
├── summarize.py     # 摘要 prompt (get_summarize_prompt)
└── mindmap.py       # 思维导图 prompt (get_mindmap_prompt)
```

每个 prompt 都是一个函数，接收参数并返回格式化的 prompt 字符串。

---

## 5. LLM 配置

### 5.1 支持的 Provider

| Provider | 模型前缀 | 默认模型 |
|----------|----------|----------|
| `anthropic` | `anthropic/` | `claude-sonnet-4-20250514` |
| `openai` | 无 | `gpt-4o` |
| `custom` | `openai/` | 用户自定义 |

### 5.2 通用参数
```python
temperature = 0.1  # 低温度保证输出稳定性
# max_tokens 不设置，使用模型默认值
```

### 5.3 Custom Provider 配置
支持任何 OpenAI Compatible API：
```json
{
    "llm_provider": "custom",
    "custom_api_base": "http://localhost:11434/v1",
    "custom_model": "llama3",
    "custom_api_key": "optional"
}
```

---

## 6. 性能优化配置

### 6.1 低显存环境
```json
{
    "whisper_batch_size": 8,
    "diarization_batch_size": 8,
    "whisper_compute_type": "int8"
}
```

### 6.2 长音频处理 (2小时+)
系统会自动：
1. 在 VAD 静音点分片 (每30分钟)
2. 减小 batch_size (>60分钟时降至8)
3. 分段转录后合并

### 6.3 跳过人声分离
对于清晰语音录音：
```json
{
    "task_type": "pipeline",
    "source": "path/to/file",
    "options": {
        "skip_separation": true
    }
}
```

### 6.4 并行润色性能

润色阶段使用并行处理，性能参数：
- **chunk_size**: 64 段/块 (适合大多数 LLM 上下文)
- **overlap**: 16 段 (保证连贯性)
- **max_concurrency**: 8 (根据 API 限制可调整)

对于 1000 段字幕：
- 串行处理: ~21 次 LLM 调用 (顺序执行)
- 并行处理: ~21 次 LLM 调用 (最多 8 个同时执行)

---

## 7. API 快速参考

### 创建管线任务
```http
POST /api/tasks
Content-Type: application/json

{
    "task_type": "pipeline",
    "source": "https://youtube.com/watch?v=xxx",
    "options": {
        "skip_separation": false
    }
}
```

### 任务状态轮询
```http
GET /api/tasks/{task_id}
```

### 直接调用单步骤
```http
POST /api/pipeline/download     # 下载
POST /api/pipeline/separate     # 分离
POST /api/pipeline/transcribe   # 转录
POST /api/pipeline/polish       # 润色
POST /api/pipeline/summarize    # 摘要
POST /api/pipeline/mindmap      # 思维导图
```

---

## 8. 任务状态流转

```
pending → queued → processing → completed
                        │
                        └─→ failed
                        └─→ cancelled
```

**进度计算**:
```python
progress = completed_steps / total_steps
# total_steps = 7 (download, separate, transcribe, voiceprint, polish, analyze, archive)
```
