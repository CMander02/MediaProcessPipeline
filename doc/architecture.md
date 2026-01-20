全能媒体处理管线 (Omni-Media Processing Pipeline) v2.0 - 架构设计文档

1. 项目概述

本项目旨在构建一个全自动、本地优先的媒体处理管线。其核心目标是将非结构化的音视频数据（YouTube 视频、播客、会议录音）转化为结构化、可检索的知识（逐字稿、摘要、思维导图），并自动归档至个人知识库（如 Obsidian）。

v2.0 版本引入了 UVR5 人声分离作为前置处理，并使用 WhisperX 替换原有的 whisper.cpp，以解决背景音干扰和说话人区分（Diarization）的问题。

2. 系统架构图

核心逻辑流：采集 -> 清洗 -> 识别 -> 分析 -> 归档

(参考 pipeline_v2.mermaid)

3. 模块详细说明

阶段 1: 获取与元数据 (Ingestion & Metadata)

此阶段负责统一输入源，并提取必要的元数据供后续归档使用。

组件 A: 网络下载器 (yt-dlp)

功能: 下载 YouTube/Bilibili 等平台视频。

关键配置:

--write-info-json: 必须开启，用于提取 title, uploader, upload_date, webpage_url。

--extract-audio: 如果不需要画面（默认配置不需要画面），直接转为 m4a/wav 以节省存储。

组件 B: 本地扫描器 (Local Watcher)

功能: 监控 inbox_audio/ 文件夹。

逻辑: 计算文件 SHA256 哈希值与 index.json 比对，防止重复处理。

阶段 2: 前置信号处理 (Pre-processing)

此阶段决定了转写的质量上限。通过移除背景音乐（BGM）和噪音，大幅降低 Whisper 的幻觉率。

组件: UVR5 (通过 audio-separator CLI)

推荐模型: Kim_Vocal_2 (MDX-Net 架构)

理由: 在保留人声细节和去除背景音之间取得了最佳平衡，且推理速度快于 Ensemble 模式。

处理逻辑:

检测音频是否需要降噪（可通过元数据标签或默认开启）。

运行分离：Input -> [Vocals.wav] + [Instrumental.wav]。

丢弃 Instrumental 轨道，仅将 Vocals 传递给 WhisperX。

Python 库: pip install audio-separator

阶段 3: 核心识别引擎 (Core Recognition)

心脏模块，负责将音频转换为带有精确时间轴和角色标签的文本。

组件: WhisperX

技术栈: Faster-Whisper (ASR) + Wav2Vec2 (对齐) + Pyannote.audio (说话人分离)。

工作流:

VAD (Voice Activity Detection): 过滤静音片段，避免对空气转写。

Transcription: 使用 large-v2 或 large-v3 模型进行快速转写（Batch Inference）。

Forced Alignment: 强制将文本与音频音素对齐，将时间轴精度从“句子级”提升到“单词级”。

Diarization: 调用 Pyannote 模型聚类声纹，分配 SPEAKER_00, SPEAKER_01 标签。

关键配置:

--compute_type float16: 开启半精度加速。

--hf_token: 必须配置 HuggingFace Token 才能下载 Pyannote 模型。当然也可以通过其他方式/其他的源下载好 Pyannote 模型以绕过 Huggingface

阶段 4: AI 深度分析 (AI Processing)

纯文本处理层，由 LLM (Large Language Model) 驱动。

任务链:

Polish (润色):

输入: 带有口癖（嗯、啊）和 ASR 错误的原始文本。

目标: 修复错误，但不改变原意。

Summary (摘要):

输入: 润色后的文本。

输出: JSON 格式的结构化摘要（TLDR、关键事实、行动项）。

MindMap (思维导图):

输入: 摘要数据。

输出: Markmap 兼容的 Markdown 格式。

提示词策略: "Generate a markdown list suitable for markmap visualization, using specific indentation."

阶段 5: 分发与归档 (Archiving)

目标系统: Obsidian / 静态网站

文件结构:

生成 Markdown 文件，包含 YAML Frontmatter。

将生成的思维导图代码块嵌入 Markdown 中。

将 .srt 字幕文件和原始/处理后的音频移动到归档目录。

4. 推荐目录结构

project_root/
├── inbox/                  # [输入] 待处理的音视频文件
├── processing/             # [临时] 中间产物 (Vocals.wav, temp.json)
├── archive/                # [归档] 处理完的源文件 (按日期归档)
├── outputs/                # [输出] 最终产物
│   └── 2023-10-27(YYYY-MM-DD)/
│       ├── video_title/
│       │   ├── transcript.srt          # 字幕文件
│       │   ├── transcript_polished.md  # 润色文稿
│       │   ├── summary.md              # 包含思维导图的笔记
│       │   └── metadata.json           # 原始元数据
│       └── ...
├── models/                 # 本地模型缓存 (Whisper, UVR5)
├── logs/                   # 日志储存
└── frontend/               # 前端页面

5. 配置与硬件要求

硬件建议

由于引入了 UVR5 和 WhisperX Large 模型，建议配置独立显卡。

GPU: NVIDIA RTX 3060 (12GB VRAM) 及以上推荐。

最低要求: 8GB VRAM (可运行 Whisper Medium + UVR5)。

CPU: 对 PyTorch 预处理有影响，建议多核。

RAM: 16GB+。

软件依赖

Python: 3.10 (推荐，兼容性最好)。

FFmpeg: 必须安装并加入系统 PATH。

CUDA Toolkit: 11.8 或 12.x (取决于 PyTorch 版本)。

环境变量 (.env)

OPENAI_API_KEY=sk-...       # 用于 LLM 润色 (如果使用 GPT)
# 或者
OLLAMA_HOST=http://localhost:11434 # 如果使用本地 LLM
