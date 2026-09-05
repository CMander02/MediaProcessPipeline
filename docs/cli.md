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
mpp server start|status|stop
mpp auth check
mpp auth unlock [--token TOKEN]
mpp auth logout
mpp capabilities
mpp upgrade-ytdlp
```

全局参数：

- `--server URL`：daemon 地址。
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
```

本机文件连接远程 daemon 时，`--upload auto` 会使用 staging。`--upload always` 强制上传，`--upload never` 按服务端路径提交。一次批量最多 100 个来源，成功上传的来源会在部分上传失败时继续提交，进程返回退出码 5。

显式 staging 管理命令为 `mpp stage upload LOCAL_FILE` 和 `mpp stage delete STAGING_ID`。

`run --direct` 在当前进程执行。`server start` 启动可持续处理 `submit` 任务的独立后台进程。

## 任务

```text
mpp task list [--status STATUS] [--active] [--limit N] [--offset N] [--watch]
mpp task stats
mpp task history [--status STATUS] [--limit N] [--offset N]
mpp task steps
mpp task show REF [--timeline] [--files]
mpp task timeline REF [--follow]
mpp task watch REF
mpp task cancel REF...
mpp task pause REF...
mpp task resume REF...
mpp task rerun REF [--checkpoint|--full] [--wait]
mpp task delete REF... --yes
```

任务引用支持完整 UUID、唯一 ID 前缀以及 `@last`、`@fail`、`@run`、`@queued`、`@paused`、`@completed`、`@active`。多匹配前缀会返回候选并使用退出码 4。

兼容入口：`tasks`、`list`、`status`、`show`、`attach`、`retry`、`open`、`cancel`。

## 归档、字幕和说话人

```text
mpp archive list [--media TYPE] [--source PLATFORM] [--sort KEY]
mpp archive show REF
mpp archive files REF
mpp archive cat REF --file summary|transcript|analysis|metadata|mindmap|NAME
mpp archive export REF --file NAME --output PATH
mpp archive thumbnail REF --output PATH
mpp archive open REF
mpp archive rename REF TITLE
mpp archive delete REF... --yes
mpp archive transcript export REF [--format srt|md|txt] [--output PATH]
mpp archive transcript import REF --from PATH [--dry-run] [--yes]
mpp speaker rename TASK_REF OLD_NAME NEW_NAME [--on-conflict ask|merge|new]
```

归档引用支持 archive ID、task ID、唯一前缀、精确路径、精确标题和 `@last`。字幕导入会校验 SRT 条目，并通过 filesystem write-through 同步 SQLite artifact。

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

## 来源平台

```text
mpp source list
mpp source show PLATFORM
mpp source config set PLATFORM KEY VALUE
mpp source probe URL
mpp source collection URL
mpp source auth status bilibili|xiaohongshu|twitter
mpp source auth login xiaohongshu|twitter [--timeout SEC]
mpp source ytdlp-status
mpp source ytdlp-upgrade
```

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
mpp sync changes [--cursor N] [--limit N]
mpp sync manifest ARCHIVE_ID
mpp sync download ARCHIVE_ID RELATIVE_PATH --output PATH
mpp sync rebuild --yes

mpp pipeline download URL
mpp pipeline scan
mpp pipeline separate SERVER_AUDIO_PATH
mpp pipeline transcribe SERVER_AUDIO_PATH [--language LANG]
mpp pipeline polish [--text TEXT|--from FILE]
mpp pipeline summarize [--text TEXT|--from FILE]
mpp pipeline mindmap [--text TEXT|--from FILE] [--language LANG]
```

`fs drives/list/scan/open` 会读取 `/api/capabilities`。远程实例通过 `allow_remote_filesystem` 控制服务端文件系统能力。

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
