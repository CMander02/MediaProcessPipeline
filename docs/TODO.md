# MediaProcessPipeline 改进计划

## 问题概述

1. ~~**临时文件管理问题** - 文件残留在 data 目录下无法清理~~ ✅ 已完成
2. ~~**目录结构冗余** - inbox/archive/processing 目录不必要，应扁平化~~ ✅ 已完成
3. ~~**缺少历史记录持久化** - 任务历史仅存内存，重启丢失~~ ✅ 已完成
4. ~~**前端缺少媒体播放器** - 无法回放已处理内容和字幕~~ ✅ 已完成
5. ~~**进度显示不直观** - 进度条跳跃式更新，用户体验差~~ ✅ 已完成
6. ~~**设置页面布局** - LLM配置应左右分栏展示~~ ✅ 已完成
7. ~~**文件选择器缺失** - 所有路径输入都缺少文件系统选择交互~~ ✅ 已完成
8. ~~**长音频处理** - 超30分钟音频需VAD切分后分段处理~~ ✅ 已完成
9. ~~**LLM润色流程** - 需要两阶段：先提取元信息，再滑动窗口润色~~ ✅ 已完成

---

## 已完成的改进

### Phase 1: 数据结构重构 (Backend) ✅

#### 1.1 简化目录结构 ✅
- [x] 删除 `data/inbox`, `data/archive`, `data/processing` 概念
- [x] 所有任务输出到 `data/{task_id}/` 扁平目录
- [x] 每个任务文件夹结构:
  ```
  data/{task_id_short}_{title}/
  ├── source/           # 原始输入文件（视频/音频）
  ├── vocals.wav        # 分离后的人声
  ├── transcript.srt    # 原始转录
  ├── transcript_polished.srt  # 润色后字幕
  ├── metadata.json     # 任务元信息
  ├── analysis.json     # LLM分析结果（语言/关键词/专有名词）
  ├── summary.md        # 摘要文档
  └── mindmap.md        # 思维导图
  ```

#### 1.2 创建全局历史记录 JSON ✅
- [x] 创建 `data/history.json` 存储所有任务历史
- [x] 实现 `HistoryService` 类 (`backend/app/services/history.py`)
- [x] 应用启动时加载历史，任务完成时更新
- [x] 添加历史 API 端点

#### 1.3 修复临时文件清理 ✅
- [x] 创建 `CleanupService` (`backend/app/services/cleanup.py`)
- [x] 添加清理 API 端点:
  - `POST /api/pipeline/cleanup/{task_id}` - 清理指定任务
  - `POST /api/pipeline/cleanup` - 清理孤儿文件
  - `GET /api/pipeline/disk-usage` - 查看磁盘使用

---

### Phase 2: 长音频处理优化 (Backend) ✅

#### 2.1 VAD切分实现 ✅
- [x] 创建 `backend/app/services/preprocessing/vad_splitter.py`
- [x] 使用 Silero VAD 检测语音段落
- [x] 实现切分逻辑:
  - 计算每30分钟的边界点
  - 找到最近的VAD静音点进行切分
  - 记录每段的起始时间偏移
- [x] 返回切分后的音频片段列表

#### 2.2 分段转录与合并 ✅
- [x] 修改 `whisperx.py` 支持分段处理
- [x] 每段独立转录
- [x] 合并时修正时间戳（加上起始偏移）
- [x] 输出完整的SRT文件

---

### Phase 3: LLM润色流程重构 (Backend) ✅

#### 3.1 两阶段润色流程 ✅
- [x] **阶段1: 元信息提取** (`analyze_content()`)
  - 输入: SRT去时轴纯文本 + 标题
  - 输出 `analysis.json`
- [x] **阶段2: 滑动窗口润色** (`polish_with_context()`)
  - 窗口大小: 50行SRT
  - 重叠: 10行（确保上下文连贯）
  - 保持 `[SPEAKER_XX]` 标记不变

#### 3.2 修改现有LLM服务 ✅
- [x] 新增 `analyze_content()` 方法
- [x] 重构 `polish()` 为滑动窗口模式
- [x] 添加speaker标记保护逻辑

---

### Phase 4: 前端进度显示优化 ✅

#### 4.1 步骤式进度展示 ✅
- [x] 移除百分比进度条
- [x] 改为步骤列表显示
- [x] 当前步骤显示加载圈圈
- [x] 后端返回 `current_step`, `steps`, `completed_steps`

---

### Phase 5: 前端媒体播放器 ✅

#### 5.1 播放器组件开发 ✅
- [x] 创建 `MediaPlayer.vue` 组件
- [x] 视频播放: 使用 `<video>` 标签
- [x] 音频播放: 支持背景图 + `<audio>` 标签
- [x] 字幕同步显示:
  - 解析SRT文件 (`src/utils/srt.ts`)
  - 根据当前播放时间高亮对应字幕
  - 点击字幕跳转到对应时间点

---

### Phase 6: 设置页面重构 ✅

#### 6.1 LLM配置左右分栏 ✅
- [x] 左侧: 供应商列表 (Anthropic/OpenAI/Custom)
- [x] 右侧: 选中供应商的配置表单
- [x] 当前使用的供应商高亮

#### 6.2 文件/文件夹选择器 ✅
- [x] 后端: `GET /api/filesystem/browse` 端点
- [x] 前端: `FileSystemPicker.vue` 组件
- [x] 支持文件和目录选择模式

---

### Phase 7: 前端配置更新 ✅

#### 7.1 更新后端配置 ✅
- [x] 移除旧目录配置项
- [x] 添加 `data_root` 配置

#### 7.2 更新Settings页面 ✅
- [x] 简化路径配置为单一数据目录

---

## 新增文件列表

### 后端
- `backend/app/services/history.py` - 历史记录服务
- `backend/app/services/cleanup.py` - 清理服务
- `backend/app/services/preprocessing/vad_splitter.py` - VAD切分
- `backend/app/api/routes/filesystem.py` - 文件系统浏览API

### 前端
- `frontend/src/components/MediaPlayer.vue` - 媒体播放器
- `frontend/src/components/FileSystemPicker.vue` - 文件选择器
- `frontend/src/utils/srt.ts` - SRT解析工具

---

## 修改文件列表

### 后端
- `backend/app/core/config.py` - 简化数据目录配置
- `backend/app/api/routes/settings.py` - 更新设置结构
- `backend/app/api/routes/tasks.py` - 步骤式进度、历史集成
- `backend/app/api/routes/pipeline.py` - 添加清理端点
- `backend/app/services/analysis/llm.py` - 两阶段润色
- `backend/app/services/archiving/archive.py` - 扁平目录结构
- `backend/app/services/recognition/whisperx.py` - VAD切分集成
- `backend/app/models/task.py` - 添加步骤追踪字段
- `backend/app/main.py` - 注册新路由

### 前端
- `frontend/src/types/index.ts` - 新类型定义
- `frontend/src/views/DashboardView.vue` - 步骤式进度UI
- `frontend/src/views/SettingsView.vue` - 左右分栏LLM配置

---

## 后续优化建议

1. **Unsplash 集成** - 为音频播放添加随机背景图
2. **归档详情页重构** - 使用 MediaPlayer 组件展示内容
3. **WebSocket 实时进度** - 替代轮询获取更流畅的进度更新
4. **任务队列** - 支持多任务并行处理
5. **导出功能** - 支持导出为 Word/PDF 格式
