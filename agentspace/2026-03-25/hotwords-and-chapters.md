# 后端热词自动提取 + 视频简介时间轴

> 2026-03-25 — 构想阶段，暂不执行

## 1. 热词自动提取（后端 pipeline 内）

**时机**：获得字幕之后、LLM 分析之前

**流程**：
1. 用户手动添加的热词（`task.options.hotwords`）排在最前面
2. 获得字幕文本后，将字幕分段发给 LLM，让 LLM 从内容中提取专有名词/术语
3. LLM 提取的热词追加到手动热词后面，去重
4. 合并后的完整热词列表用于后续的润色和分析

**LLM 提取 prompt 要点**：
- 输入：字幕文本片段 + 视频标题 + 简介
- 输出：专有名词列表（人名、公司名、技术术语、产品名等）
- 分段提取：长内容分段调用，合并去重
- 不限制数量，但控制质量（只要真正的专有名词）

**当前代码位置**：
- `backend/app/core/pipeline.py` — 在 TRANSCRIBE 步骤完成后、ANALYZE 步骤之前
- `task.options.get("hotwords")` — 已有人工热词
- `analysis["proper_nouns"]` — 已有合并逻辑（line 544）

## 2. 视频简介时间轴提取

**来源**：视频 description 中的时间轴（如 `00:01:19 The normal one`）

**提取方式**：
- 正则提取 `HH:MM:SS` 或 `MM:SS` 格式的时间戳 + 对应标题
- 或让 LLM 从 description 中识别章节结构（更鲁棒）
- yt-dlp 的 `info.get("chapters")` 也可能直接提供章节信息

**影响范围**（如果检测到时间轴，后续所有分段沿用）：
- **说话人分段**：diarization 结果与章节对齐，章节边界处强制切分
- **字幕分段识别和重写**：LLM 润色时按章节分组，每章节独立上下文
- **思维导图分图**：map-reduce 的 chunk 边界沿用章节而非固定字数
- **摘要分段**：按章节生成分段摘要

**数据结构**：
```python
chapters = [
    {"start": 79, "end": 2140, "title": "The normal one"},
    {"start": 2140, "end": 3126, "title": "世界总不让我做Vision"},
    ...
]
```

**当前代码中的章节**：
- `MediaMetadata.chapters` — 已有字段，yt-dlp 下载时填充（`ytdlp.py:200`）
- 但 pipeline 当前不使用 chapters
- description 中的手写时间轴需要额外解析

**实现建议**：
1. 在 download 步骤后，检查 `metadata.chapters`（yt-dlp 提供的）
2. 如果为空，用 LLM 从 `metadata.description` 中提取时间轴
3. 存入 `metadata.chapters`，后续步骤共享
4. 各步骤（分析、润色、导图）的分段逻辑增加 chapters 感知

## 涉及文件

| 文件 | 变更 |
|------|------|
| `backend/app/core/pipeline.py` | 热词提取步骤 + chapters 提取 + 传递给后续步骤 |
| `backend/app/services/analysis/` | 分析/润色/导图增加 chapters 感知 |
| `backend/app/services/recognition/subtitle_processor.py` | 字幕重写按章节分组 |
| `backend/app/models/task.py` | MediaMetadata.chapters 已有 |
