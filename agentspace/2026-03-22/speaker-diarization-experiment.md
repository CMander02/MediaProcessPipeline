# 说话人分割实验与平台字幕集成

## 背景

调研了 [baoyu-youtube-transcript](https://github.com/JimLiu/baoyu-skills/tree/main/skills/baoyu-youtube-transcript) 的方案，核心思路：当平台已有字幕时，不做 ASR，直接用 LLM 做说话人标注 + 标点 + 分段。

## 实验

### 测试视频
- YouTube: `rIwgZWzUKm8` — 张小珺 × 谢赛宁 7小时马拉松访谈（有手动中英文字幕）
- B站本地: 同一视频的 B站下载版（有 AI 字幕 SRT + NFO 元数据）

### 方案对比

#### v1: 简单标注（先合并字幕，再标注说话人）
- 13315 原始事件 → 1482 合并行 → 22 chunks
- 问题：合并太激进导致无标点长段、说话人切换点丢失
- 耗时 ~90s

#### v2: Baoyu-style（保留细粒度，LLM 同时做说话人 + 标点 + 分段）
- 13315 原始段 → 99 chunks（150段/chunk, 15 overlap）
- LLM 一步完成：说话人识别 + 标点添加 + 段落重组
- 输出 1450 段落，张小珺/谢赛宁准确识别
- 耗时 ~50min（每 chunk ~30s）

### 关键结论

1. **v2 远优于 v1** — 保留细粒度让 LLM 自己决定如何分段，效果好得多
2. **说话人识别准确** — 从视频元数据（标题、频道、简介）+ 文本线索（自称、称呼）推断，不需要声纹
3. **标点质量好** — 中文标点正确率高，分段合理
4. **纯 LLM 方案** — 不需要 GPU，不需要 pyannote/WhisperX
5. **chunk 参数** — 150 segments/chunk, 15 overlap 是实验验证的最佳值
6. **max_tokens=8192** — DeepSeek 的限制，紧凑输出格式（范围标注而非逐行 JSON）可绕过

### Prompt 设计要点

- 保留所有口语词（语气词、重复、笑声）
- 说话人三级优先：元数据 → 文本线索 → 通用标签
- 连续发言省略说话人标注
- 输出格式：`[HH:MM:SS → HH:MM:SS] **说话人:** 带标点文本`
- 已知说话人列表传递给后续 chunks 保持一致性

## 集成

实验验证后，已将 v2 方案集成到主 pipeline：

### 新建文件
- `backend/app/services/recognition/subtitle_processor.py` — 字幕 LLM 处理核心
- `backend/app/services/ingestion/local.py` — 本地 SRT/NFO 发现

### 修改文件
- `backend/app/services/ingestion/ytdlp.py` — 增加 `download_subtitles()`
- `backend/app/core/pipeline.py` — 字幕路径集成
- `backend/app/core/settings.py` — `prefer_platform_subtitles`, `subtitle_languages`

### 流程
```
有字幕: DOWNLOAD(+字幕) → SEPARATE(跳过) → TRANSCRIBE(LLM) → ANALYZE → POLISH(跳过) → ARCHIVE
无字幕: DOWNLOAD → SEPARATE → TRANSCRIBE(ASR) → ANALYZE → POLISH(LLM) → ARCHIVE
```

### 字幕来源
1. YouTube/B站 URL → yt-dlp 下载字幕（json3 优先）
2. 本地视频 → 同目录搜索 `{stem}.srt`
3. 上传文件 → `find_original_file()` 在 `D:\Video` 等目录搜索原始位置

### 待优化
- 并行处理：将 75 chunks 分 4 组并行，先做说话人发现 pass，预计 50min → 13min
