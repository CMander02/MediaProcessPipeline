# MediaProcessPipeline

媒体处理管线 - 将音视频转化为结构化知识。

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
│       ├── App.vue             # 根组件
│       ├── main.ts             # Vue 入口
│       ├── style.css           # UnoCSS + 自定义样式
│       ├── api/                # API 客户端
│       │   └── index.ts
│       ├── composables/        # Vue composables
│       │   ├── useTasks.ts     # 任务管理
│       │   └── useSettings.ts  # 设置管理
│       ├── components/         # UI 组件
│       │   ├── AppLayout.vue   # 侧边栏布局
│       │   ├── StatCard.vue    # 统计卡片
│       │   ├── TaskRow.vue     # 任务行
│       │   ├── TaskDetailDialog.vue
│       │   ├── ArchiveCard.vue
│       │   └── ArchiveDetailDialog.vue
│       ├── views/              # 页面组件
│       │   ├── DashboardView.vue
│       │   ├── TasksView.vue
│       │   ├── ArchivesView.vue
│       │   └── SettingsView.vue
│       └── types/              # TypeScript 类型
│           └── index.ts
├── data/                       # 数据目录
│   ├── inbox/                  # 待处理
│   ├── processing/             # 中间文件
│   ├── outputs/                # 输出
│   └── archive/                # 归档
└── scripts/                    # 开发脚本
```

## 快速开始

```powershell
.\scripts\setup.ps1    # 安装依赖
.\scripts\dev.ps1      # 启动开发服务器
.\scripts\stop.ps1     # 停止服务
```

## 开发

```bash
# 后端
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev
```

## API 端点

### 任务管理
- `POST /api/tasks` - 创建任务
- `GET /api/tasks` - 列出任务
- `GET /api/tasks/{id}` - 获取任务详情
- `POST /api/tasks/{id}/cancel` - 取消任务

### 管道操作
- `POST /api/pipeline/download` - 下载媒体
- `POST /api/pipeline/scan` - 扫描 inbox
- `POST /api/pipeline/separate` - 人声分离
- `POST /api/pipeline/transcribe` - 转录音频
- `POST /api/pipeline/polish` - 润色文本
- `POST /api/pipeline/summarize` - 生成摘要
- `POST /api/pipeline/mindmap` - 生成思维导图
- `GET /api/pipeline/archives` - 列出归档

## 前端功能

- **Dashboard**: 任务创建表单，统计概览，活动任务实时进度
- **Tasks**: 任务列表，筛选，详情查看
- **Archives**: 归档浏览，转录/摘要/思维导图查看
- **Settings**: API 配置，转录设置，路径配置，处理选项

## 技术栈

- **Backend**: Python 3.13, FastAPI, uv
- **Frontend**: Vue 3.5 + UnoCSS + Element Plus + TypeScript
- **AI**: WhisperX, UVR5 (audio-separator), Anthropic/OpenAI
