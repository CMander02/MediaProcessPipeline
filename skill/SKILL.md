---
name: media-process-pipeline
description: 用 MediaProcessPipeline (mpp) 把音视频转成结构化知识——转写、人声分离、说话人分离、摘要、思维导图。当用户给出一个本地媒体文件路径、视频/播客 URL（YouTube、Bilibili、小红书、小宇宙等），或说"处理这个视频/音频"、"转写"、"出摘要"、"出字幕"、"跑一下 mpp"、"提交到管线"、"看一下任务"、"重试上一条"、"打开输出目录"、"改一下管线配置/模型/key"时使用本 skill。也用于诊断 daemon 是否在线、查看任务进度。
---

# MediaProcessPipeline 使用 skill

帮用户用本仓库的 `mpp` CLI 把音视频转成结构化知识产物（字幕 / 转录 / 摘要 / 思维导图）。环境与模型默认已配置好——直接调用命令，不要让用户重新装东西。

## 基本调用方式

CLI 入口（任选其一，**优先 PowerShell wrapper**，因为这是 Windows 项目）：

- `scripts/mpp.ps1 <command> ...`（Windows，推荐）
- `scripts/mpp <command> ...`（POSIX wrapper）
- `cd backend && uv run python -m app.cli <command> ...`（无 wrapper 时的兜底）

Daemon 默认监听 `http://127.0.0.1:18000`。`run / submit / attach / retry` 会自动后台拉起 daemon，无需先 `serve`。

## 用户意图 → 命令映射

按用户说的话直接挑命令，**不要堆解释**：

| 用户说的话 | 你执行 |
|---|---|
| "处理一下这个视频 `<path/url>`" / "转写一下 / 出摘要" | `mpp run <source>` |
| "后台跑就行，不用等" / "我自己晚点看" | `mpp submit <source>` 然后告诉用户 task id |
| "看一下任务" / "现在在跑啥" | `mpp tasks` |
| "实时看进度" | `mpp tasks --watch` 或 `mpp attach @run` |
| "刚才那个怎么样了" / "上次的结果" | `mpp show @last` |
| "失败的那条" | `mpp show @fail` |
| "重试上一条失败的" | `mpp retry @fail` |
| "打开输出文件夹" | `mpp open @last` |
| "把摘要打出来" | `mpp show @last --summary` |
| "字幕呢" / "转录文本" | `mpp show @last --transcript` |
| "取消" | `mpp cancel @run` |
| "改 xxx 配置" / "换模型 / 换 API key" | `mpp config set <key> <value>` |
| "现在配置是啥" | `mpp config list` 或 `mpp config list --group llm` |
| "环境有没有问题 / 跑不动" | `mpp doctor` |
| "daemon 起来没" | `mpp ping` |

## 任务引用语法

任何接受 task 的命令都支持：

- 完整 ID 或 ID 前缀（≥4 位）：`mpp show a1b2c3d4`
- `@last`：最近一条任务（任何状态）
- `@run`：当前正在跑的任务
- `@fail`：最近一条失败的任务

引用解析在 daemon 离线时会回退读 SQLite，所以 `tasks / show / config list` 离线也能用。

## 常用 run 参数

```
mpp run <source> [--no-sep] [--speakers N] [--hotwords "词1,词2"] [--force-asr] [--quiet]
```

- `--no-sep`：跳过 UVR 人声分离（纯人声音源/省时）
- `--speakers N`：固定说话人数（不传则自动检测）
- `--hotwords "a,b,c"`：ASR 热词
- `--force-asr`：忽略平台原生字幕，强制本地 ASR（B 站/YouTube 字幕质量差时用）
- `--quiet`：只输出最终路径，适合脚本捕获

## 配置 (`mpp config`)

- 分组：`llm` / `asr` / `uvr` / `diarization` / `subtitle` / `paths` / `security` / `bilibili` / `concurrency`
- `mpp config list --group llm`、`mpp config get llm_provider`、`mpp config set llm_provider deepseek`
- `set` 自动把 `true/false/数字` 转成对应类型
- 密钥项（API key / SESSDATA 等）`list` 会脱敏显示，但 `set` 接受明文
- 未知 key 会报错并给出近似建议——**不要瞎猜 key 名**，先 `config list` 或 `config get` 验证

## 操作守则

- **先做事，再解释**：用户给了 URL/路径，直接 `mpp run`，不要先反问。除非真的有歧义（比如批量任务）。
- **优先 `@last / @fail / @run`**，比让用户复制 ID 友好。
- **后台运行**：长任务用 `submit` + 后续 `attach @run`，比 `run` 阻塞终端好。Ctrl+C 脱离 `run/attach` 时任务会继续，要告诉用户怎么重新挂回去。
- **不要重启 daemon**：`run/submit/attach/retry` 会按需自动拉起；不要主动 `serve`，除非用户要前台日志。
- **报告产出位置**：任务完成后 `mpp show @last` 的输出目录就是用户要的东西，必要时 `mpp open @last`。
- **失败先看 doctor**：任务失败且原因不明显时跑 `mpp doctor`，别盲目重试。
- **不要乱碰 `--reload`**：长任务时 reload 会断 worker（见用户 feedback）。
- 引用代码或确认行为时，以 `backend/app/cli/main.py` 为准，注释和老 README 可能过时。

## 深度参考

需要更细的命令语义时再读，不要无脑全读：

- `references/entrypoints-and-conventions.md` — 入口、全局 flag、输出约定
- `references/task-lifecycle.md` — `run` / `submit` / `attach` / `retry` 细节
- `references/inspection-and-control.md` — `tasks` / `show` / `open` / `cancel` / `ping`
- `references/environment-and-serving.md` — `serve` / `doctor` / 启动流程
- `references/configuration.md` — `config` 子命令、字段分组、类型强转规则

## 典型一气呵成示例

用户："帮我处理一下 https://www.bilibili.com/video/BV1xx411c7mD，出摘要"

```
scripts/mpp.ps1 run https://www.bilibili.com/video/BV1xx411c7mD
# 跑完后：
scripts/mpp.ps1 show @last --summary
scripts/mpp.ps1 open @last
```

用户："换成 deepseek 跑摘要"

```
scripts/mpp.ps1 config set llm_provider deepseek
scripts/mpp.ps1 config get llm_provider
```

用户："上次那条失败了，重试一下"

```
scripts/mpp.ps1 retry @fail
```
