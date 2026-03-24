# B站下载与登录态方案调研

## 结论

**yt-dlp + cookie 文件是最佳方案**，不需要额外集成 BBDown。

- BBDown 唯一作用：QR 码扫码登录获取 cookie（一次性操作）
- 转换为 Netscape cookie 文件后，yt-dlp 完全接管所有下载
- Cookie 有效期约 6 个月

## 验证结果

### yt-dlp + cookies 能力（已验证）

| 功能 | 无登录 | 有 cookie |
|------|--------|----------|
| 视频 360P-480P | 可 | 可 |
| 视频 720P-1080P | 不可 | 可 |
| 视频 4K/HDR（需VIP） | 不可 | 可 |
| AI 字幕 (ai-zh) | 不可 | **可** |
| 弹幕 (danmaku.xml) | 可 | 可 |
| info.json 元数据 | 可 | 可（更完整） |
| 封面/描述/评论 | 可 | 可 |

### Cookie 获取流程

1. `BBDown.exe login` → 手机 B站 APP 扫码
2. Cookie 保存到 `BBDown.data`（纯文本，分号分隔键值对）
3. 转换脚本将 BBDown.data → Netscape cookies.txt：

```python
cookie_str = open("BBDown.data").read()
pairs = {}
for part in cookie_str.split(";"):
    if "=" in part:
        k, v = part.strip().split("=", 1)
        pairs[k] = v

expiry = pairs.pop("Expires", "0")
pairs.pop("gourl", None)
pairs.pop("first_domain", None)

lines = ["# Netscape HTTP Cookie File", ""]
for name, value in pairs.items():
    lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t{expiry}\t{name}\t{value}")

with open("bilibili_cookies.txt", "w") as f:
    f.write("\n".join(lines) + "\n")
```

4. yt-dlp 使用：`yt-dlp --cookies bilibili_cookies.txt <URL>`

### 浏览器 cookie 读取（备选）

| 浏览器 | `--cookies-from-browser` | 限制 |
|--------|------------------------|------|
| Firefox | 可用，不需关闭浏览器 | 需要在 Firefox 中登录 B站 |
| Chrome | 需要完全关闭浏览器 | DPAPI 加密，Chrome 130+ 可能有问题 |
| Edge | DPAPI 解密失败 | 不推荐 |

## 工具对比

### yt-dlp vs BBDown

| 维度 | yt-dlp | BBDown |
|------|--------|--------|
| 语言 | Python | C# (.NET) |
| 元数据输出 | **info.json 极丰富** | 无结构化输出 |
| 字幕下载 | 需 cookie 登录 | 需关闭 skip-ai |
| 视频质量 | 登录态下全部支持 | 全部支持（8K/杜比/FLAC） |
| pipeline 集成 | **已有** | 需额外对接 |
| API server | 无 | 有（serve 模式） |
| 评论/互动数据 | 有 | 无 |
| 跨平台源支持 | YouTube/B站/数百个站 | 仅 B站 |

### BBDown serve 模式

启动：`BBDown.exe serve -l http://localhost:58682`

| 端点 | 方法 | 用途 |
|------|------|------|
| `/add-task` | POST | 提交下载（JSON body，最少传 Url） |
| `/get-tasks` | GET | 所有任务 |
| `/get-tasks/{id}` | GET | 单任务查询 |
| 支持 webhook | - | 任务完成回调 |

适合批量下载场景，当前不需要。

### downkyicore (yaobiao131/downkyicore)

- C# + AvaloniaUI 跨平台 **GUI**，无 CLI，不适合 pipeline
- 原 downkyi 作者删库，这是社区重写版
- 字幕 API 用 `/x/player/wbi/v2`（带 WBI 签名），下载所有字幕不过滤 AI
- BBDown 也用同一个 API，但默认 `--skip-ai=true` 跳过 AI 字幕

## B站字幕说明

- B站 CC 字幕为 **AI 生成**（语言标记 `ai-zh`），不是人工标注
- **无说话人标注**——平台本身不提供 speaker label
- 当前 pipeline 的 LLM 说话人识别+标点添加是正确路径

## Pipeline 集成建议

最小改动：
1. `data/bilibili_cookies.txt` 存放 cookie 文件
2. `settings.json` 增加 `bilibili_cookies_path` 配置项
3. `ingestion/ytdlp.py` 在调用 yt-dlp 时检测并添加 `--cookies` 参数
4. Cookie 过期时重新用 BBDown 扫码导出（约每 6 个月一次）
