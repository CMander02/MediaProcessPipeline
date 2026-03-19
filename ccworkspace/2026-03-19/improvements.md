# 改进建议 - 2026-03-19

## P0 - 核心体验

### 1. 前端重写 → Gradio (已确定)
- 用 Gradio 重写前端，Python 全栈，省去前后端分离维护成本
- 核心功能：转录结果查看页，音频播放器+字幕高亮联动，说话人标注
- Gradio 内置 Audio/Video/File 组件，适合媒体处理场景
- 设计参考：飞书妙记交互模式

### 2. SSE 替代轮询
- FastAPI `StreamingResponse` 推送步骤进度
- 前端 `EventSource` 接收
- 去掉 200ms 轮询

### 3. 任务持久化
- 内存 dict → SQLite
- 重启后恢复任务列表和历史
- 处理中任务的崩溃恢复机制

---

## P1 - 管线优化

### 4. 去掉 Qwen3-ASR 冗余 VAD
- 直接用 Qwen3-ASR-Toolkit 处理长音频（内置 VAD 分块）
- 保留 WhisperX 路径的 VAD（WhisperX 自带）
- 保留 UVR5 人声分离（用户场景多样）

### 5. 并行化 LLM 调用
- analyze 和 summarize 并行执行
- polish 等 analyze 完成后开始
- summarize 和 mindmap 应接收 polished transcript 而非原始

### 6. 扩大分析范围
- analyze 分块提取合并（不只取前 8000 字）
- 关键词扩展到 10-20 个
- 增加时间轴关键词/章节标记提取

### 7. UVR 模型路径自动发现
- 扫描 UVR 常见安装路径
- 检测已存在的模型文件
- Settings 页提供"扫描"按钮

---

## P2 - 工程改善

### 8. 统一 CLI
- CLI 支持完整 pipeline（7 步）
- 参数：`--skip-separation`, `--skip-diarization`, `--language`, `--asr-backend`
- 走 task 系统，输出到标准 `data/{task_id}/` 目录
- 进度条显示

### 9. 错误处理
- LLM 调用添加重试（指数退避）
- 步骤级错误恢复（不是整个 task 失败）
- ffmpeg 安装检测

### 10. 资源管理
- 切换 ASR 后端时释放旧模型显存
- 中间文件清理可配置（保留/删除）
- CUDA 显存使用监控

### 11. 安全
- 修复硬编码 URL (`DashboardView.vue:116`)
- Settings API Key 加密存储
- CORS 白名单

---

## P3 - 未来方向

### 12. Qwen3-Omni 一站式方案
- 一次推理产出 transcript + analysis + summary
- 30B MoE 模型，显存要求高
- 适合有大显存的场景

### 13. TEN VAD 替代 Silero VAD
- 精度更高、延迟更低（8.6x faster）
- 277KB-731KB vs Silero 2.2MB
- 如果保留外部 VAD 步骤时替换

---

## 技术选型笔记

### Gradio (已选定)
- HuggingFace 收购
- Python 直接写前端，无需前后端分离
- 内置 Audio/Video/File/Chatbot/DataFrame 等组件
- 支持自定义 CSS 和 JavaScript
- 可嵌入 FastAPI 应用 (`gr.mount_gradio_app`)
- 适合快速迭代，音频组件开箱即用

### Qwen3-ASR vs WhisperX
- Qwen3-ASR：中文优势明显（WER 约为 Whisper 的 1/2-1/3），原生长音频支持
- WhisperX：多语言稳定，生态成熟
- 两者都保留，用户可切换
