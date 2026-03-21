# 项目全面分析 - 2026-03-19

## 代码审查范围

前后端全部源码、CLI 脚本、配置文件、prompt 模板。

---

## 一、界面问题

### 与飞书妙记的差距

飞书妙记核心交互：音频播放器+字幕高亮联动、说话人彩色标注、时间线可视化、侧边搜索/评论。

当前项目缺失：
- 没有转录结果查看/编辑页面——任务完成后只能去 `data/` 目录手动打开文件
- 没有音视频播放器与字幕联动
- 没有说话人可视化（diarization 做了但前端不展示）
- Dashboard 只有输入框+进度条

### 具体 UI 问题

| 问题 | 位置 | 说明 |
|------|------|------|
| DashboardView 800 行，统计卡片只显示内存中的任务 | `DashboardView.vue` | 重启后归零 |
| SettingsView 1250 行 | `SettingsView.vue` | 可拆分子组件；UVR 路径可自动扫描本地安装目录 |
| 暗色模式不完整 | `style.css:427` `.option-card:hover` 硬编码 `#ebeef3` | 暗色下刺眼 |
| 文件上传 URL 硬编码 | `DashboardView.vue:116` | 与 API client 的 `VITE_API_URL` 不一致 |
| Archives 页无法查看内容 | 全局 | 点击归档应能在线查看 transcript/summary |

---

## 二、处理管线问题

### 冗余步骤

1. **Qwen3-ASR 双重 VAD**：`qwen3_asr.py:272-401` 手动 Silero VAD 切分 + `recognition/__init__.py:68` 的 `split_long_audio`。Qwen3-ASR 原生支持 20 分钟音频，Toolkit 内置 VAD 分块，外部 VAD 可省略。

2. **LLM 串行调用**：analyze → polish → summarize 三步串行。analyze 和 summarize 互不依赖，可并行。polish 依赖 analyze 结果。

### 关键词提取不足

- `llm.py:101` 只取前 8000 字符做分析，长视频后半段丢失
- prompt 只要求 5 个关键词，对一小时技术讲座不够
- 没有时间轴关键词提取（话题分布在哪些时间段）
- 没有利用 ASR word-level confidence

### 内容分析质量

- `tasks.py:477-478` summarize 和 mindmap 接收原始 transcript（含识别错误+口语填充词），不是 polished 版本
- JSON 解析失败静默返回默认值 (`llm.py:125-137`)
- 长音频段间时间戳对齐可能有偏移问题 (`recognition/__init__.py:90`)

---

## 三、前后端同步问题

- 200ms 轮询 (`useTasks.ts:5`) 过于频繁，任务处理需 5-30 分钟
- 没有 WebSocket / SSE
- `setTimeout(() => fetchTasks(), 300)` hack (`useTasks.ts:31`)
- 内存任务存储 (`tasks.py:26`)，重启丢失
- Settings 路径依赖 CWD，多标签不同步

---

## 四、CLI 与前端不一致

- `transcribe_local.py` 只做提取音频+转录 (2/7 步)
- 缺少 `--skip-separation`, `--language`, `--full-pipeline` 等参数
- 输出硬编码 `data/manual_task/`，不走 task 系统
- CLI 直接调用 service 层，前端走 API——两条路径行为可能不一致

---

## 五、其他问题

- CORS `allow_origins=["*"]`
- API Key 通过 HTTP 明文传输
- 切换 ASR 后端时旧模型不释放显存
- 中间文件清理过于激进（无法重试/调试）
- 没有 LLM 调用重试机制
- ffmpeg 未检查是否安装
- dev.ps1 仅 Windows PowerShell，无 bash 等价物
