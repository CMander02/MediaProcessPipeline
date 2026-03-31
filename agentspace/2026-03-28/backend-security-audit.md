# 后端安全审计报告

日期: 2026-03-28

纯代码审计，不依赖设计文档，只审查当前后端实现。

---

## CRITICAL

### 1. 路径穿越：字符串前缀检查可绕过

**位置:** `api/routes/filesystem.py:113, 137, 193`

```python
if not str(file_path).startswith(str(data_root)):
```

如果 `data_root` 为 `D:\Video\MediaProcessPipeline`，攻击者请求 `D:\Video\MediaProcessPipelineEvil\secrets.txt` 即可绕过检查——因为字符串前缀匹配通过了。

**正确做法:**

```python
file_path.relative_to(data_root)  # 不是子路径则抛 ValueError
```

注意 `pipeline.py:158` 的 `delete_archive` 和 `rename_archive` 已经用了 `relative_to()`，但 filesystem 路由没有。

**影响端点:** `GET /api/filesystem/read`, `POST /api/filesystem/write`, `GET /api/filesystem/media`

---

### 2. 文件系统浏览无访问控制

**位置:** `api/routes/filesystem.py:20-99, 214-241`

`GET /api/filesystem/browse` 没有任何路径限制。任何 HTTP 客户端可以浏览整个文件系统：`C:\Windows\System32`、用户主目录、`.ssh` 密钥目录等。`scan-folder` 同理，可递归扫描任意目录。

**影响端点:** `GET /api/filesystem/browse`, `GET /api/filesystem/scan-folder`

---

### 3. API 密钥明文返回

**位置:** `api/routes/settings.py:21-24`

`GET /api/settings` 返回完整的 `RuntimeSettings`，包括 `anthropic_api_key`、`openai_api_key`、`custom_api_key`、`hf_token`、Bilibili session cookie 等所有敏感字段。任何能访问 18000 端口的人都能拿到全部密钥。

---

### 4. 所有端点无认证

**位置:** `main.py`

整个 API 零认证——无 token、无 API key、无 session。CORS 虽然限制了 `localhost:5173` origin，但 CORS 只对浏览器生效。机器上的任何进程（或局域网内如果端口暴露）都可以调用所有端点，包括:
- `DELETE /api/pipeline/archives` 删除归档
- `PATCH /api/settings` 覆盖 API 密钥
- `POST /api/tasks` 提交任意任务

---

## HIGH

### 5. 上传文件名清理不完整

**位置:** `api/routes/pipeline.py:48`

```python
safe_name = file.filename.replace("/", "_").replace("\\", "_")
```

只替换了 `/` 和 `\`，未处理:
- Windows 保留设备名（`CON`, `NUL`, `AUX`, `PRN` 等）
- 文件名以 `.` 结尾（Windows 会自动去除，可能导致覆盖）
- `filename` 来自客户端 `Content-Disposition`，完全受攻击者控制

---

### 6. SSE 事件流注入

**位置:** `api/routes/tasks.py:200`

```python
yield f"data: {{\"task_id\": \"{task_id}\", ... \"message\": \"{task.message or ''}\"}}"
```

`task.message` 直接插入 SSE JSON 字符串，未做转义。如果 message 包含 `"` 或 `\n\n`，可以破坏 JSON 结构或注入新的 SSE 事件。如果前端不校验 SSE payload，则可能导致 XSS。

---

### 7. XML 外部实体注入 (XXE)

**位置:** `services/ingestion/local.py:78`

```python
tree = ET.parse(nfo_path, parser=ET.XMLParser(encoding="utf-8"))
```

`xml.etree.ElementTree` 默认不防护 XXE。恶意 `.nfo` 文件可以引用外部实体读取任意文件或触发 SSRF。应使用 `defusedxml` 或禁用实体展开。

---

### 8. SSRF：下载端点接受任意 URL

**位置:** `api/routes/pipeline.py:94-97`, `services/ingestion/ytdlp.py`

`POST /api/pipeline/download` 和 `GET /api/pipeline/probe` 接受任意 URL 传给 yt-dlp。攻击者可以:
- 探测内网服务（`http://localhost:*`、`http://169.254.169.254` 云元数据）
- 通过 `file:///` URI 读取本地文件（yt-dlp 支持）
- 向任意内部主机发起请求

无 URL 校验或黑名单机制。

---

## MEDIUM

### 9. 缩略图端点缺少路径校验

**位置:** `api/routes/pipeline.py:278-339`

`GET /api/pipeline/archives/thumbnail?path=...` 未校验 `path` 是否在 `data_root` 下。攻击者可以:
- 指向任意目录探测文件存在性（404 vs 200）
- 通过 `FileResponse` 提供任意图片文件
- 触发 ffmpeg 处理任意视频文件

---

### 10. SQLite 跨线程共享无锁保护

**位置:** `core/database.py:66`

```python
_connection = sqlite3.connect(str(db_path), check_same_thread=False)
```

SQLite 连接以 `check_same_thread=False` 跨线程共享，但没有 `threading.Lock` 保护写操作。FastAPI 的 async worker 和 `asyncio.to_thread()` 下，并发写入可能导致数据库损坏或 `OperationalError: database is locked`。WAL 模式缓解了部分竞争，但不保证 Python 层面单连接并发写的安全性。

---

### 11. 硬编码搜索路径泄露本地结构

**位置:** `services/ingestion/local.py:136-153`

`find_original_file()` 硬编码搜索 `D:/Video`、`E:/Video`、`~/Videos`、`~/Downloads` 并递归 glob。在大目录下可能很慢，且暴露了文件系统布局假设。上传的文件可能被静默关联到用户未打算处理的文件。

---

### 12. 无速率限制 / 无请求大小限制

- 所有端点无速率限制——客户端可以提交数千个任务，耗尽 GPU/LLM 资源，或淹没下载队列
- `POST /api/pipeline/upload` 无文件大小限制——可耗尽磁盘空间
- `POST /api/pipeline/cleanup` 传 `max_age_hours=0` 可立即删除所有孤立目录

---

### 13. 错误信息泄露内部路径

多个文件的错误响应包含 `str(e)`，可能暴露完整文件系统路径、堆栈信息或内部状态：
- `filesystem.py:93`: browse 响应中 `"error": str(e)`
- `pipeline.py:142`: BBDown stderr 写入错误信息
- `tasks.py:200`: SSE 中 task message 包含内部路径

---

## LOW

### 14. data_root 可通过 API 修改

**位置:** `core/settings.py:90`, `PATCH /api/settings`

恶意客户端可以 `PATCH /api/settings` 设置 `{"data_root": "C:\\"}`，之后所有「路径必须在 data_root 内」的检查都形同虚设——整个文件系统变为可读写。这是一个权限提升原语。

---

### 15. 文件名作为命令参数的潜在风险

所有 `subprocess.run()` 调用使用列表参数（好）且未设 `shell=True`（好）。但传给 ffmpeg、BBDown、yt-dlp 的文件名来自用户输入或下载元数据。如果文件名以 `-` 开头，ffmpeg 可能将其解释为参数。建议用 `--` 分隔选项和参数，或路径前加 `./`。

---

### 16. 无 CSRF 防护

CORS 中间件允许 `localhost:5173` 带凭据访问，但无 CSRF token 机制。恶意网站在同一浏览器中可能对 `localhost:18000` 发起「简单请求」（如 form POST），不受 CORS 预检拦截。

---

## 汇总

| # | 严重度 | 问题 |
|---|--------|------|
| 1 | **CRITICAL** | 路径穿越：filesystem read/write/media 的字符串前缀检查可绕过 |
| 2 | **CRITICAL** | 文件系统浏览无限制（browse + scan-folder） |
| 3 | **CRITICAL** | API 密钥通过 GET /api/settings 明文返回 |
| 4 | **CRITICAL** | 所有端点无认证 |
| 5 | HIGH | 上传文件名清理不完整 |
| 6 | HIGH | SSE 事件流注入（task.message 未转义） |
| 7 | HIGH | NFO XML 解析存在 XXE 风险 |
| 8 | HIGH | SSRF：下载/探测端点接受任意 URL |
| 9 | MEDIUM | 缩略图端点缺少路径校验 |
| 10 | MEDIUM | SQLite 跨线程共享无锁保护 |
| 11 | MEDIUM | 硬编码搜索路径泄露本地结构 |
| 12 | MEDIUM | 无速率限制 / 无上传大小限制 |
| 13 | MEDIUM | 错误信息泄露内部路径 |
| 14 | LOW | data_root 可通过 API 修改（权限提升） |
| 15 | LOW | 文件名作为 subprocess 参数的潜在风险 |
| 16 | LOW | 无 CSRF 防护 |

## 修复优先级建议

1. **认证** (#4) — 加上认证机制后，远程/局域网攻击面直接消除
2. **路径穿越** (#1) — 将 `startswith` 改为 `relative_to()`
3. **限制浏览范围** (#2) — browse/scan-folder 限制在 data_root 内
4. **密钥脱敏** (#3) — GET /api/settings 返回时遮蔽敏感字段
5. **缩略图路径校验** (#9) — 添加 data_root 边界检查
6. 其余按优先级逐步修复
