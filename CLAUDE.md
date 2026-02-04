# MediaProcessPipeline

媒体处理管线 - 将音视频转化为结构化知识。

## 项目结构

```
MediaProcessPipeline/
├── backend/                    # Python 后端 (FastAPI)
│   ├── run.py                  # 启动入口 (端口 18000)
│   ├── app/
│   │   ├── main.py             # FastAPI 应用
│   │   ├── core/config.py      # 配置
│   │   ├── models/             # 数据模型
│   │   ├── api/routes/         # API 路由
│   │   │   ├── tasks.py        # 任务管理 + 管线处理
│   │   │   ├── pipeline.py     # 管道操作
│   │   │   ├── settings.py     # 运行时设置
│   │   │   └── filesystem.py   # 文件浏览
│   │   └── services/           # 业务逻辑
│   │       ├── ingestion/      # yt-dlp 下载
│   │       ├── preprocessing/  # UVR5 人声分离 + VAD 切分
│   │       ├── recognition/    # WhisperX / Qwen3-ASR 转录
│   │       ├── analysis/       # LLM 润色/摘要/思维导图
│   │       └── archiving/      # 归档导出
│   └── .env.example
├── frontend/                   # Vue 3 + UnoCSS + Element Plus
│   └── src/
│       ├── App.vue             # 根组件
│       ├── main.ts             # Vue 入口
│       ├── style.css           # UnoCSS + 自定义样式
│       ├── api/                # API 客户端
│       │   └── index.ts
│       ├── composables/        # Vue composables
│       │   ├── useTasks.ts     # 任务管理 (200ms 轮询)
│       │   └── useSettings.ts  # 设置管理
│       ├── components/         # UI 组件
│       │   ├── AppLayout.vue   # 侧边栏布局
│       │   ├── FileSystemPicker.vue  # 本地文件选择器
│       │   └── ...
│       ├── views/              # 页面组件
│       │   ├── DashboardView.vue   # 首页 (任务创建 + ASR切换)
│       │   ├── TasksView.vue
│       │   ├── ArchivesView.vue
│       │   └── SettingsView.vue
│       └── types/              # TypeScript 类型
│           └── index.ts
├── data/                       # 数据目录 (任务输出)
│   └── {task_id}_{title}/      # 每个任务的输出目录
│       ├── source/             # 原始媒体文件
│       ├── metadata.json       # 媒体元数据
│       ├── analysis.json       # LLM 分析结果
│       ├── transcript.srt      # 原始转录
│       ├── transcript_polished.srt  # 润色后转录
│       ├── transcript_polished.md   # Markdown 格式
│       └── summary.md          # 摘要 + 思维导图
└── scripts/                    # 开发脚本
    ├── dev.ps1                 # 启动开发服务器
    └── stop.ps1                # 停止服务
```

## 快速开始

```powershell
.\scripts\dev.ps1      # 启动开发服务器 (后端 18000, 前端 5173)
.\scripts\stop.ps1     # 停止服务
```

## 开发

```bash
# 后端
cd backend
uv sync
uv run python run.py --reload

# 前端
cd frontend
npm install
npm run dev
```

## 处理管线

1. **下载媒体** - yt-dlp 下载 YouTube/Bilibili 或复制本地文件
2. **分离人声** - UVR5 (audio-separator) 去除背景音乐
3. **转录音频** - WhisperX 或 Qwen3-ASR (可切换)
4. **分析内容** - LLM 提取元数据 (语言/主题/说话人等)
5. **润色字幕** - LLM 滑动窗口纠错 + 添加标点
6. **生成摘要** - LLM 生成摘要和思维导图
7. **归档保存** - 输出到任务目录，可选同步 Obsidian

处理完成后自动清理中间文件 (人声分离输出、VAD切分音频)。

## ASR 后端

支持两种 ASR 后端，可在 Dashboard 或 Settings 中切换：

- **WhisperX** (默认) - 基于 Whisper，支持多语言，自带 VAD 和对齐
- **Qwen3-ASR** - 基于 Qwen3，使用 Silero VAD 进行语音分割

长音频 (>30分钟) 会自动在静音点切分后并行处理。

## API 端点

### 任务管理
- `POST /api/tasks` - 创建任务
- `GET /api/tasks` - 列出任务
- `GET /api/tasks/{id}` - 获取任务详情
- `POST /api/tasks/{id}/cancel` - 取消任务

### 管道操作
- `POST /api/pipeline/upload` - 上传本地文件
- `POST /api/pipeline/download` - 下载媒体
- `POST /api/pipeline/separate` - 人声分离
- `POST /api/pipeline/transcribe` - 转录音频
- `GET /api/pipeline/archives` - 列出归档

### 设置
- `GET /api/settings` - 获取设置
- `PUT /api/settings` - 更新设置

## 技术栈

- **Backend**: Python 3.11+, FastAPI, uv
- **Frontend**: Vue 3.5 + UnoCSS + Element Plus + TypeScript
- **ASR**: WhisperX, Qwen3-ASR
- **Audio**: UVR5 (audio-separator), Silero VAD
- **LLM**: Anthropic / OpenAI / DeepSeek (可配置)

## 注意事项

- 后端端口固定为 **18000** (不是 8000)
- Windows 上使用 UTF-8 编码处理 emoji 文件名
- 前端轮询间隔 200ms，任务创建后立即开始轮询
