# 本地上传媒体文件 Pipeline 审计

日期: 2026-03-28
状态: **全部已修复**

## 审计范围

前端文件选择/拖放 → upload API → task 创建 → DOWNLOAD step → 后续 pipeline 处理

---

## BUG: DOWNLOAD step 对本地文件不标记完成 + 无 checkpoint guard

**严重程度: HIGH — 会导致本地文件重复复制/音频重复提取**

`pipeline.py` 第 409-525 行, DOWNLOAD step 的结构:

```python
# 第 410 行
if PipelineStep.DOWNLOAD in done:
    _restore_metadata()
    _restore_audio_paths()
else:
    await _update_step(task, PipelineStep.DOWNLOAD)

# 第 417 行 — 注意: 这里不在 else 块内!
if _looks_like_local_path(task.source):
    shutil.copy2(source_path, dest_source)      # 重复复制!
    _extract_audio_from_video(...)               # 重复提取!
    ...
else:
    # URL 下载分支
    ...
    await _update_step(task, PipelineStep.DOWNLOAD, completed=True)  # 只有URL分支标记完成
```

### 问题 1: 本地文件分支没有 checkpoint 跳过逻辑
当 DOWNLOAD 已在 `done` 中时, 第 410-413 行确实执行了 restore, 但第 417 行 `if _looks_like_local_path()` **无条件执行**, 导致:
- 文件被重复 `shutil.copy2` 到 task_dir
- ffmpeg 重复提取音频
- 对大视频文件会造成显著性能浪费

### 问题 2: 本地文件分支不标记 DOWNLOAD completed
`_update_step(task, PipelineStep.DOWNLOAD, completed=True)` 只在 URL 分支 (第 524 行) 调用。本地文件处理完后 **从不标记 DOWNLOAD 为完成**。后果:
- 后端重启后, 本地文件任务永远被放回 download queue 而不是 gpu queue
- completed_steps 永远不包含 DOWNLOAD, 进度计算偏低

### 问题 3: 本地文件不写 metadata.json 也不发 file_ready
URL 分支在第 521-522 行写入 metadata.json 并发送 SSE event。本地文件分支跳过了这两步, 导致:
- 前端结果页在 DOWNLOAD 阶段无法获取元数据
- metadata.json 的 status 停留在 task 创建时的 "queued" 直到 ARCHIVE 步骤

### 问题 4: 本地文件 `has_subtitle` 未正确设置
`has_subtitle = platform_subtitle is not None` 只在 URL 分支第 518 行赋值。本地文件分支虽然搜索了 `platform_subtitle`, 但从未设置 `has_subtitle = True`。后果:
- 本地视频即使找到了旁路字幕, 仍会走完整 ASR 流程
- UVR 分离不会被跳过 (即使有字幕)

### 修复建议
整个 `if _looks_like_local_path() ... else ...` 块需要包裹在 `if PipelineStep.DOWNLOAD not in done:` 中, 并且本地文件分支结束时需要:
```python
has_subtitle = platform_subtitle is not None
write_metadata_json(task_dir, metadata, status="processing")
await _emit_file_ready(task, "metadata.json", str(meta_path))
await _update_step(task, PipelineStep.DOWNLOAD, completed=True)
```

---

## BUG: ANALYZE step 缩进错误 — LLM 调用无条件执行

**严重程度: HIGH — 已跳过的 ANALYZE step 仍会调用 LLM**

`pipeline.py` 第 649-694 行:

```python
    else:                                              # 第 649 行
        await _update_step(task, PipelineStep.ANALYZE)
        video_metadata = {
        "uploader": metadata.uploader,                 # ← 缩进回退到 function 级!
        ...
    }
    # 下面全部在 function 级缩进, 不在 else 块内
    mindmap_metadata = { ... }
    analysis, summary, mindmap = await asyncio.gather(...)  # 每次都调用!
    ...
        await _update_step(task, PipelineStep.ANALYZE, completed=True)  # 这行在 if mindmap 里
```

`video_metadata` dict 的结束 `}` 在第 656 行回退到与 `if/else` 同级, 导致:
- `mindmap_metadata` 构建、`asyncio.gather(analyze, summarize, mindmap)` 调用、文件写入 **全部在 checkpoint 恢复时也会执行**
- LLM API 调用被浪费
- `_update_step(ANALYZE, completed=True)` 被错误地放在 `if mindmap:` 条件内, 如果 mindmap 生成失败则 ANALYZE 永不标记完成

---

## 问题: 上传文件无自动清理机制

**严重程度: MEDIUM**

上传文件保存在 `{data_root}/uploads/`, 但:
1. `cleanup_orphaned_files()` 只扫描 `data_root` 的直接子目录, `uploads/` 目录下的文件不会被单独清理
2. 任务完成后, pipeline 通过 `shutil.copy2` 已将文件复制到 task_dir, 但 uploads 中的原始上传文件 **永远不会被自动删除**
3. 只有在用户手动删除 archive 时 (delete_archive 端点) 才会尝试删除上传源文件
4. 任务失败时 `cleanup_failed_task()` 只删除 task_dir, 不删除 uploads 中的源文件

### 磁盘开销
大视频可能 2-10 GB。每个本地上传文件存在三份:
- `uploads/{name}` — 上传的原始文件 (永不自动删除)
- `{task_dir}/{name}` — copy2 的副本
- `{task_dir}/{title}.wav` — 提取的音频

### 建议
- DOWNLOAD step 成功后删除 uploads 中的源文件 (或改 copy2 为 move)
- cleanup_orphaned_files 增加对 uploads 目录内孤儿文件的扫描

---

## 问题: 大文件上传无进度反馈

**严重程度: LOW-MEDIUM**

前端上传时:
- `fetch()` 不提供上传进度回调
- UI 只显示 "上传中..." 旋转图标, 无百分比/速度/剩余时间
- 10 GB 文件上传可能需要数十秒到数分钟 (本地磁盘 → 本地 API, 实际是磁盘拷贝)
- 用户可能误以为卡住而重复操作

### 建议
- 使用 XMLHttpRequest 或 fetch + ReadableStream 获取上传进度
- 或: 对于本地文件, 跳过上传, 直接将路径传给 task.create (文件夹队列模式已经这么做了)

---

## 问题: 上传和文件夹队列两条路径的行为不一致

**严重程度: MEDIUM**

| 功能 | 文件上传 (拖放/选择) | 文件夹队列 |
|------|---------------------|-----------|
| 文件传递方式 | 上传到 uploads/ → 传服务器路径 | 直接传本地路径 |
| 额外磁盘开销 | 文件在 uploads/ 多一份 | 无额外开销 |
| 进度反馈 | 有上传阶段 | 无 |
| 原始文件关联 | uploads/ 中找不到 .srt/.nfo | 能找到旁路字幕/NFO |

关键差异: 文件上传后, 源文件变成 `uploads/{safe_name}`, pipeline 在 DOWNLOAD step 用 `find_local_subtitle(source_path)` 搜索字幕时, `source_path` 指向 uploads 目录而非原始位置。虽然代码通过 `find_original_file()` 做了回退搜索, 但这依赖文件名匹配, 在文件名被 sanitize 后可能失败。

### 建议
统一为: 前端拖放的文件也直接用 Electron 获取的本地路径, 避免先上传再复制。仅在纯 Web 模式 (无法访问本地文件系统) 才走 upload 流程。

---

## 问题: 前端 removeQueued 不取消正在上传的文件

**严重程度: LOW**

`submit-page.tsx` 第 93 行:
```typescript
const removeQueued = (id: string) => setQueuedFiles((prev) => prev.filter((f) => f.id !== id))
```

用户点击 X 移除正在上传的文件时, 只从列表中移除了 UI 状态, 但 `uploadAndQueue` 中的 `fetch` 请求仍在后台运行。文件最终还是会上传到服务器, 占用磁盘和带宽。

### 建议
使用 AbortController 管理每个上传请求, removeQueued 时调用 `abort()`。

---

## 问题: 文件夹队列对话框的批量提交无取消/限流机制

**严重程度: LOW-MEDIUM**

`folder-queue-dialog.tsx` 第 129-146 行:
```typescript
for (const file of mediaFiles) {
    await api.tasks.create(file.path, options)
    count++
    setSubmitProgress(count)
}
```

- 串行逐个提交, 但无取消机制 — 一旦开始就无法停止
- 无并发限制 (虽然是串行的, 但如果文件夹有 500 个文件, 提交过程很长)
- 单个失败会被静默跳过, 用户不知道哪些失败了
- `done` 状态在所有提交完成后才设置, 然后调用 `onSubmitted()` 导航走了, 用户看不到失败详情

---

## 问题: 文件类型验证不够严格

**严重程度: LOW**

前端:
- `<input accept="video/*,audio/*">` 只是浏览器建议, 不是强制的
- 拖放的 MIME type 过滤在 `useDropZone` 中, 但文件扩展名和 MIME type 可能不匹配

后端:
- `/api/pipeline/upload` 不验证文件内容类型, 只做文件名 sanitize
- 任何文件都可以上传, 在 pipeline DOWNLOAD step 才会因扩展名不支持而 `raise ValueError`
- 但此时文件已经上传并占用磁盘

### 建议
upload 端点增加扩展名白名单校验, 拒绝非媒体文件。

---

## 问题: 并发上传 + 并发 task 创建的竞态条件

**严重程度: LOW**

前端允许:
1. 用户拖入多个文件 → 并发上传 → 全部 ready 后点提交
2. 多文件提交时是串行 `for` 循环创建 task

但存在边界情况:
- 用户在文件还在上传时就能输入 URL 并提交 (URL 不受 `anyUploading` 限制... 实际上 `canSubmit` 检查了 `!anyUploading`, 所以这个没问题)
- `create_task_dir` 的去重逻辑 (`dir_name (2)`) 不是原子操作, 两个并发的同名任务可能创建同一目录 (但下载队列本身是串行的, 所以 dir 创建时序不太可能冲突)

**实际风险很低**, 但值得注意。

---

## 总结: 优先级排序

| 优先级 | 问题 | 影响 |
|--------|------|------|
| **P0** | DOWNLOAD step 本地文件无 checkpoint guard + 不标记完成 | 重启后重复处理, 进度错误 |
| **P0** | ANALYZE step 缩进错误导致 LLM 无条件调用 | 浪费 API 费用, checkpoint 无效 |
| **P1** | 本地文件 `has_subtitle` 未设置 | 有字幕也走 ASR |
| **P1** | 上传文件无自动清理 | 磁盘空间泄漏 |
| **P2** | 上传 vs 文件夹队列行为不一致 | 上传文件丢失字幕关联 |
| **P2** | 大文件上传无进度 | 用户体验差 |
| **P3** | removeQueued 不取消上传 | 浪费带宽 |
| **P3** | 文件夹批量提交无取消机制 | 无法中断 |
| **P3** | 上传端点不验证文件类型 | 非媒体文件占用磁盘 |
