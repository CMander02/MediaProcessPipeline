# MediaProcessPipeline

媒体处理管线 - 将音视频转化为结构化知识。

## 功能特性

- **媒体下载**: 支持 YouTube、Bilibili 等平台视频下载 (yt-dlp)
- **本地文件处理**: 支持直接处理本地音视频文件
- **人声分离**: UVR5 (audio-separator) 分离人声和背景音乐
- **语音转录**: WhisperX 高精度转录，支持说话人分离
- **智能润色**: LLM 滑动窗口润色，修正错字、添加标点
- **内容分析**: 自动提取关键信息、生成摘要和思维导图
- **Obsidian 导出**: 生成 Markdown 格式文稿，支持 Obsidian 同步

## 项目结构

```
MediaProcessPipeline/
├── backend/                    # Python 后端 (FastAPI)
│   ├── app/
│   │   ├── main.py             # 入口
│   │   ├── core/config.py      # 配置
│   │   ├── models/             # 数据模型
│   │   ├── api/routes/         # API 路由
│   │   └── services/           # 业务逻辑
│   │       ├── ingestion/      # yt-dlp 下载
│   │       ├── preprocessing/  # UVR5 人声分离
│   │       ├── recognition/    # WhisperX 转录
│   │       ├── analysis/       # LLM 润色/摘要
│   │       └── archiving/      # Obsidian 导出
│   └── .env.example
├── frontend/                   # Vue 3 + UnoCSS + Element Plus
│   └── src/
│       ├── App.vue
│       ├── views/              # 页面组件
│       └── components/         # UI 组件
├── data/                       # 数据目录 (处理输出)
└── scripts/                    # 开发脚本
```

## 快速开始

### 环境要求

- Python 3.13+
- Node.js 18+
- FFmpeg
- CUDA (可选，GPU 加速)

### 安装

```bash
# 克隆项目
git clone https://github.com/your-repo/MediaProcessPipeline.git
cd MediaProcessPipeline

# 后端
cd backend
uv sync
cp .env.example .env  # 配置环境变量

# 前端
cd ../frontend
npm install
```

### 启动开发服务器

```bash
# 后端 (端口 8000)
cd backend
uv run uvicorn app.main:app --reload --port 8000

# 前端 (端口 5173)
cd frontend
npm run dev
```

或使用脚本：

```bash
./scripts/dev.sh      # 启动开发服务器
./scripts/setup.sh    # 安装依赖
```

## API 端点

### 任务管理
- `POST /api/tasks` - 创建处理任务
- `GET /api/tasks` - 列出任务
- `GET /api/tasks/{id}` - 获取任务详情
- `POST /api/tasks/{id}/cancel` - 取消任务

### 管道操作
- `POST /api/pipeline/upload` - 上传本地文件
- `POST /api/pipeline/download` - 下载媒体
- `POST /api/pipeline/separate` - 人声分离
- `POST /api/pipeline/transcribe` - 转录音频
- `POST /api/pipeline/polish` - 润色文本
- `POST /api/pipeline/summarize` - 生成摘要
- `GET /api/pipeline/archives` - 列出归档

### 设置
- `GET /api/settings` - 获取设置
- `PUT /api/settings` - 更新设置

## 处理流程

1. **下载/导入** - 从 URL 下载或导入本地文件
2. **人声分离** - UVR5 分离人声，删除背景音乐
3. **语音转录** - WhisperX 转录，支持多语言和说话人分离
4. **内容分析** - LLM 提取标题、主题、关键词等元数据
5. **文本润色** - LLM 滑动窗口处理，修正转录错误
6. **生成摘要** - 生成 TL;DR 和关键要点
7. **归档输出** - 保存 SRT、Markdown、摘要等文件

## 输出文件

每个任务在 `data/{task_id}/` 下生成：

- `source/` - 原始媒体文件
- `metadata.json` - 媒体元数据
- `analysis.json` - LLM 分析结果
- `transcript.srt` - 原始转录 SRT
- `transcript_polished.srt` - 润色后 SRT
- `transcript_polished.md` - 干净的 Markdown 文稿
- `summary.md` - 摘要和思维导图

## 配置

在前端 Settings 页面或 `data/settings.json` 中配置：

- **LLM**: API 密钥、模型选择 (OpenAI/Anthropic/本地)
- **WhisperX**: 模型大小、语言、设备
- **UVR**: 模型选择、模型目录
- **路径**: 数据目录、Obsidian Vault 路径

## 技术栈

- **Backend**: Python 3.13, FastAPI, uv
- **Frontend**: Vue 3.5, UnoCSS, Element Plus, TypeScript
- **AI**: WhisperX, UVR5 (audio-separator), LiteLLM
- **下载**: yt-dlp, FFmpeg

## TODO

1. 所有模型做两套加载方式：CUDA (GPU) 和纯 CPU+内存，支持无显卡环境运行
2. 实验 VLM + FFmpeg 直接识别视频硬字幕的可行性
3. B站、YouTube 直接下载已有字幕的可行性（yt-dlp 可能不够，需要调研替代方案）
4. 拓展更多媒体文件类型，最终将这套系统发展为完整的多媒体信息库
5. ~~数据目录迁移到其他磁盘~~ — 已迁移到 `D:/Video/MediaProcessPipeline`
6. 超长视频的处理方案
7. 超长音频并行切分多片段进行说话人分离，后续合并结果
8. 调研 llama.cpp 加载模型的可行性（优点：免装 CUDA，编译即用；缺点：新模型可能需要重新编译 llama.cpp）

## License

MIT
