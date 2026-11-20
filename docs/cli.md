# MPP CLI 使用参考

MPP CLI 与 Web GUI 共用 FastAPI daemon、SQLite 任务库、runtime settings 和归档目录。默认服务地址为 `http://localhost:18000`。

命令注册与全局参数位于 `app.cli.main`。`app.cli.commands` 中的 execution 管理提交与跟踪，direct_execution 管理当前进程执行，task_views 管理兼容查询命令，config_values 管理配置值读写，maintenance 管理服务与诊断，root_support 提供客户端和任务引用解析。各命令通过共享 CliContext 读取输出模式、连接参数和交互设置。

## 启动与连接

```powershell
# Windows PowerShell
.\scripts\mpp.ps1 server start
.\scripts\mpp.ps1 server status
.\scripts\mpp.ps1 server stop --yes

# 前台调试
.\scripts\mpp.ps1 serve

# 远程服务器
$env:MPP_TOKEN = "..."
.\scripts\mpp.ps1 --server https://mpp.example --token-env MPP_TOKEN auth check
```

服务与访问命令：

```text
mpp ping
mpp doctor
mpp server start|status|stop|nodes
mpp auth check
mpp auth unlock [--token TOKEN]
mpp auth logout
mpp capabilities
mpp upgrade-ytdlp
```

全局参数：

- `--server URL`：daemon 地址。
- `--remote`、`--local`：分别连接已配置的协调服务器和本机 daemon。
- `--token TOKEN`、`--token-env NAME`：Bearer token。
- `--timeout SEC`：普通 HTTP 请求超时。
- `--json`：单结果 JSON envelope。
- `--jsonl`：事件流或批量结果逐行 JSON。
- `--plain`、`--no-color`：终端兼容输出。
- `--quiet`：精简结果。
- `--no-input`：关闭交互提示。
- `--yes`：确认当前修改或删除操作。
- `--debug`：输出异常诊断。

环境变量支持 `MPP_SERVER_URL`、`MPP_API_TOKEN`、`MPP_TIMEOUT` 和 `MPP_NO_INPUT`。

## 提交媒体

```powershell
# 单个 URL 或文件，等待完成
mpp run SOURCE

# 多文件、目录、glob、来源清单
mpp submit a.mp4 b.mp3
mpp submit .\media --recursive
mpp submit ".\media\*.mp4"
mpp submit --from-file .\sources.txt

# 常用处理参数
mpp run SOURCE --force-asr --skip-separation --speakers 2
mpp run SOURCE --hotword MPP --hotword Codex --option temperature=0.2
mpp submit SOURCE --webhook https://example.test/hook --wait

# Bilibili 合集
mpp source collection URL
mpp submit URL --collection all
mpp submit URL --collection ITEM_ID_1,ITEM_ID_2
mpp submit URL --episodes 1-5,8 --collection-folder
mpp submit URL --folder "课程笔记" --move-existing
mpp run SOURCE --only-transcribe --no-diarize
```

`run` 和 `submit` 默认按可识别的 URL 来源标识匹配已有任务或归档，并显示 `reused` 或 `skipped`。每次独立提交的本机文件使用各自的归档标识。等待模式仅跟踪新提交的任务。使用 `--force-duplicate` 可创建新任务。使用 `--folder` 指定虚拟文件夹，使用 `--move-existing` 可移动匹配的已有内容。

使用 `--diarize/--no-diarize`、`--speaker-review/--no-speaker-review`、`--polish/--no-polish`、`--summary/--no-summary`、`--mindmap/--no-mindmap` 和 `--detail/--no-detail` 控制任务阶段。`--only-transcribe` 关闭润色、分析、摘要、导图和详情生成。

启用 `cli_use_remote_server` 时，默认开启的 `cli_local_files_stay_local` 会将本机磁盘文件交给本机 daemon，将 URL 交给协调服务器。该规则适用于 `--upload auto` 且未显式指定连接或执行节点的提交。向远端提交本机文件时，`--upload auto` 会使用 staging。`--upload always` 强制上传，`--upload never` 按服务端路径提交。一次批量最多 100 个来源。部分来源处理失败时，CLI 会列出已接受的任务和错误，并使用退出码 5。

显式 staging 管理命令为 `mpp stage upload LOCAL_FILE` 和 `mpp stage delete STAGING_ID`。

`run --direct` 在当前进程执行。`server start` 启动可持续处理 `submit` 任务的独立后台进程。

### 指定执行节点

```powershell
mpp server nodes
mpp server prefer WORKER [--offline-policy fallback_server|wait|fallback_any_online]
mpp --json server nodes
mpp run URL --target-worker SilentField
mpp submit URL --remote --target-worker local
mpp submit URL --local
```

`--target-worker` 接受节点 ID、唯一显示名称或 `local`。显示名称匹配忽略大小写。多个节点同名时，CLI 会列出候选 ID。`local` 指当前连接的 daemon 所在机器。

显式提供的 `--server` 或 `MPP_SERVER_URL` 优先。省略这两项时，`--remote` 使用 `remote_server_url`，`--local` 使用本机 daemon。启用 `cli_use_remote_server` 后，任务提交和查询默认使用 `remote_server_url`。连接配置的协调服务器时，CLI 默认使用 `remote_api_token`，显式提供的 token 优先。

省略 `--target-worker` 时，`run` 和 `submit` 使用 `cli_default_worker_id`。如果本机未开启 `remote_execution_enabled`，指定非 `local` 的目标会自动使用已配置的协调服务器。可通过 `mpp config list --group remote` 查看相关配置。`run --direct` 与 `--target-worker`、`--remote` 互斥。

## 任务

```text
mpp task list [--status STATUS] [--active] [--limit N] [--offset N] [--watch]
mpp task stats
mpp task history [--status STATUS] [--limit N] [--offset N]
mpp task steps
mpp task show REF [--timeline] [--files]
mpp task timeline REF [--follow]
mpp task watch REF
mpp task cancel [REF...] [--all-active]
mpp task pause REF...
mpp task resume [REF...] [--all-paused]
mpp task rerun REF [--checkpoint|--full] [--from speaker_review|polish|analyze] [--wait]
mpp task delete [REF...] [--status failed|cancelled] --yes
mpp task cleanup-duplicates [--apply] [--yes]
mpp retry REF [--new]
mpp retry --all-failed
```

任务引用支持完整 UUID、唯一 ID 前缀以及 `@last`、`@fail`、`@run`、`@queued`、`@paused`、`@completed`、`@active`。多匹配前缀会返回候选并使用退出码 4。

`task rerun --from` 复用原始字幕或已选平台字幕，重新生成指定阶段及其下游产物。启用润色的任务使用 `--from analyze` 时需要已有润色稿。`retry` 恢复暂停任务，或从失败和取消任务的 checkpoint 重跑。`--new` 按原任务参数另建任务。

兼容入口：`tasks`、`list`、`status`、`show`、`attach`、`retry`、`open`、`cancel`。

## 归档、字幕和说话人

```text
mpp read REF [--view overview|summary|detail|mindmap|chapters|transcript|raw|notes|source|analysis]
mpp read REF --at HH:MM:SS [--window SEC] [--speaker NAME]
mpp search QUERY [--limit N] [--speaker NAME] [--refresh]
mpp archive list [--search TEXT] [--folder FOLDER] [--media TYPE] [--source PLATFORM] [--sort KEY]
mpp archive show REF
mpp archive files REF
mpp archive cat REF --file summary|transcript|analysis|metadata|mindmap|NAME
mpp archive export REF [--file NAME|--format md|srt|txt|zip] --output PATH
mpp archive slim [REF...] [--all] [--policy text|playback|all] [--apply] [--yes]
mpp archive edit REF [TARGET] [--from FILE]
mpp archive cue REF CUE_INDEX [--text TEXT] [--speaker NAME] [--delete] [--yes]
mpp archive dedupe [--apply] [--yes]
mpp archive reconcile [--status-only]
mpp archive artifacts REF [--repair mirror|restore-file] [--names FILES] [--yes]
mpp archive thumbnail REF --output PATH
mpp archive open REF
mpp archive rename REF TITLE
mpp archive delete REF... --yes
mpp archive transcript export REF [--format srt|md|txt] [--output PATH]
mpp archive transcript import REF --from PATH [--dry-run] [--yes]
mpp speaker inspect REF [NAME]
mpp speaker list REF
mpp speaker pending REF
mpp speaker rename REF OLD_NAME NEW_NAME [--on-conflict ask|merge|new] [--regenerate]
mpp speaker autofill REF [--wait]
mpp speaker repair REF [--mode all|failed] [--wait]
mpp speaker preview REF
mpp speaker assign REF NAME --cue 2,4-5
mpp speaker apply-preview REF [--cues 2,4-5|--all] [--yes]
mpp speaker confirm REF
```

归档引用支持 archive ID、task ID、唯一前缀、精确路径、精确标题和 `@last`。字幕导入会校验 SRT 条目，并通过 filesystem write-through 同步 SQLite artifact。

`read`、`search` 和 `speaker inspect` 支持本机 daemon 离线时读取本地资料库。`read --view transcript` 支持按时间范围、章节、字幕编号、说话人和文本筛选，也可用 `--bilingual zh,en` 显示双语。`archive slim` 默认预览将清理的媒体文件及空间，确认后保留文本产物。按文件夹执行时使用 `mpp folder batch slim --folder FOLDER`。

### 虚拟文件夹

```text
mpp folder list
mpp folder show [FOLDER] [--unclassified] [--page N] [--limit N] [--search QUERY]
mpp folder create NAME
mpp folder rename FOLDER NAME
mpp folder delete FOLDER [--yes]
mpp folder move REF... (--to FOLDER|--unclassified)
mpp folder batch ACTION [REF...] [--folder FOLDER] [--apply] [--yes]
```

文件夹引用支持 ID、唯一 ID 前缀和名称。`list` 和 `show` 支持本机离线读取。`batch` 的操作包括 `retry`、`checkpoint-rerun`、`full-rerun`、`slim`、`export` 和 `delete`，默认只生成预览。删除文件夹会保留归档并移入未分类。使用 `batch delete --apply` 则会删除预览选中的内容记录和归档。

## 配置、Provider、模型和 flow

```text
mpp config list [--group GROUP]
mpp config get KEY
mpp config set KEY VALUE
mpp config patch --from settings-patch.json
mpp config replace --from settings.json [--dry-run] --yes
mpp config export [--redacted] [--output FILE]
mpp config validate [--from FILE]
mpp config preset api-flow|local-models [--dry-run]

mpp provider list
mpp provider show ID
mpp provider enable ID
mpp provider disable ID
mpp provider add ID [--type TYPE] [--api-base URL] [--api-key KEY]
mpp provider update ID KEY=VALUE...
mpp provider delete ID --yes
mpp provider oauth-status ID
mpp provider login ID
mpp provider balance ID

mpp model list [--provider ID] [--capability TYPE]
mpp model catalog PROVIDER [--capability TYPE]
mpp model siliconflow-catalog
mpp model sync PROVIDER
mpp model infer MODEL_ID [--provider ID] [--type TYPE]
mpp model add PROVIDER MODEL_ID [--type TYPE]
mpp model remove PROVIDER MODEL_ID --yes
mpp model local-asr
mpp model detect-uvr
mpp model binding list
mpp model binding set CAPABILITY PROVIDER MODEL_ID
mpp model binding unset CAPABILITY

mpp flow list
mpp flow show ID
mpp flow use ID [--source PLATFORM]
```

`config set` 适合标量字段。`config patch`、Provider、模型和 binding 命令负责结构化字段。secret 在在线、离线、text 和 JSON 输出中统一掩码。

`provider login` 在 daemon 所在机器的终端可启动 Provider CLI。连接远端 daemon 时，命令仅查询登录状态并在认证后同步模型。

## 来源平台

```text
mpp source list
mpp source show PLATFORM
mpp source config set PLATFORM KEY VALUE
mpp source probe URL
mpp source collection URL
mpp source lookup URL...
mpp source auth status bilibili|xiaohongshu|twitter
mpp source auth login bilibili|xiaohongshu|twitter [--timeout SEC]
mpp source auth set-cookie PLATFORM [--from FILE]
mpp source auth set-cookie youtube --browser chrome|edge|firefox|safari
mpp source ytdlp-status
mpp source ytdlp-upgrade
```

`source auth login bilibili` 在终端显示二维码和登录链接，并等待扫码确认。`set-cookie` 支持 `bilibili`、`xiaohongshu`、`twitter` 和 `youtube`。省略 `--from` 时从标准输入读取。YouTube 文件使用 Netscape cookies.txt 格式。`--browser` 指 daemon 所在机器上的浏览器。

## 知识库、声纹、日志和存储

```text
mpp kb search QUERY [--top-k N] [--platform P] [--uploader ID]
mpp kb stats
mpp kb reindex --yes

mpp voiceprint list
mpp voiceprint show PERSON_ID
mpp voiceprint update PERSON_ID [--name NAME] [--notes TEXT]
mpp voiceprint merge DST_ID SRC_ID --yes
mpp voiceprint delete PERSON_ID --yes
mpp voiceprint sample SAMPLE_ID --output FILE
mpp voiceprint samples PERSON
mpp voiceprint sample-delete SAMPLE_ID [--yes]
mpp voiceprint enroll NAME --from-task REF --speaker SPEAKER
mpp voiceprint enroll NAME --from-file SERVER_AUDIO [--start SEC] [--end SEC]

mpp logs list
mpp logs show [FILE] [--cursor N] [--max-bytes N]
mpp logs tail [FILE] [--follow]

mpp storage usage
mpp storage clean [--task REF | --older-than HOURS] [--dry-run|--apply] [--yes]
```

`storage clean` 默认预览候选路径、字节数和原因。`--apply --yes` 执行清理。按任务清理仅接受 failed 与 cancelled 状态。

## 高级文件系统、同步和原子管线

```text
mpp fs drives
mpp fs list SERVER_PATH [--mode file|directory|all]
mpp fs scan SERVER_PATH [--recursive|--no-recursive]
mpp fs read SERVER_PATH
mpp fs write SERVER_PATH --from LOCAL_FILE [--dry-run] --yes
mpp fs download SERVER_PATH --output LOCAL_FILE
mpp fs open SERVER_PATH

mpp sync status
mpp sync pull
mpp sync push [REF...] [--all]
mpp sync changes [--cursor N] [--limit N]
mpp sync manifest ARCHIVE_ID
mpp sync download ARCHIVE_ID RELATIVE_PATH --output PATH
mpp sync rebuild --yes
mpp sync transfer TASK_REF --to URL [--include-media]

mpp pipeline download URL
mpp pipeline scan
mpp pipeline separate SERVER_AUDIO_PATH
mpp pipeline transcribe SERVER_AUDIO_PATH [--language LANG]
mpp pipeline polish [--text TEXT|--from FILE]
mpp pipeline summarize [--text TEXT|--from FILE]
mpp pipeline mindmap [--text TEXT|--from FILE] [--language LANG]
```

`fs drives/list/scan/open` 会读取 `/api/capabilities`。远程实例通过 `allow_remote_filesystem` 控制服务端文件系统能力。

`sync transfer` 随已完成归档传输所属文件夹、归属关系和该归档的固定封面状态。使用 `--to remote` 可连接已配置的协调服务器。可用全局 `--server` 指定归档来源。

在归档来源机器上使用 `mpp --local sync push --all` 可向已配置的协调服务器推送待同步归档和文件夹。指定任务引用会重新上传对应的已完成归档。`sync pull` 从已配置的远端拉取归档与文件夹变更。默认同步文本、封面和图文配图。

## 设备与系统

```text
mpp device list
mpp device set all|uvr|asr|diarization|aligner|moss|llm auto|gpu|cpu|cuda|mps|metal
mpp system status
mpp update check [--force]
mpp update download [--asset NAME]
```

`device list` 显示 daemon 硬件和各阶段的设备配置。`system status` 显示显存、已加载模型、队列、磁盘、运行时长和最近失败。`update download` 在 daemon 上下载并校验安装包，并输出文件路径、SHA-256 和安装说明。

## 输出与退出码

`--json` 使用 `{ok,data,meta}` envelope。`--jsonl` 适合 `task list --watch`、`task timeline --follow`、`task watch` 和 `logs tail --follow`。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 运行失败或服务端错误 |
| 2 | 参数、输入或确认缺失 |
| 3 | 连接或认证错误 |
| 4 | 资源状态冲突、目标不唯一或 capability 拒绝 |
| 5 | 批量操作部分成功 |
| 130 | 用户中断 |

Shell completion 使用 Typer 内置的 `--install-completion` 与 `--show-completion`。
