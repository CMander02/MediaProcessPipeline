# 资料库布局、迁移与恢复

`config.json` 位于项目根目录。它的 `data_root` 指向当前资料库，CLI 与 HTTP 使用同一配置入口。

全新资料库使用以下布局，`.mpp-layout.json` 保存布局版本 2：

```text
data_root/
  archives/<title>/
  state/tasks.db
  state/kb.db
  state/voiceprints/library.db
  state/voiceprints/clips/
  state/auth/
  tmp/<用途>/
  logs/
  backups/
```

现有根目录包含 `tasks.db`、旧归档或旧状态目录时，按原布局打开。标题及同名编号保留，归档身份继续使用原有 `archive_id`。内部持久化路径使用相对于资料库的路径，对外 API 返回可用绝对路径。资料库之外的源文件保留绝对路径，并在任务记录中标为 `external_source`。

## 预览

在项目根目录执行以下命令；也可使用 `scripts/mpp.ps1`：

```powershell
uv run python -m app.cli storage migrate
uv run python -m app.cli storage migrate --source D:/Video/Library --target E:/Library
```

需要让 Python 找到后端包时，在 `backend/` 目录执行。预览列出来源、目标、文件数、文件大小、逐目录操作、数据库记录数量、冲突及外部源文件。数据库大小为预览时数据库主文件大小；WAL、迁移备份及复制过程的额外空间另计。预览不改变库布局。

## 执行与重试

1. 先取消或完成活动、暂停任务，停止本地 daemon 及使用该库的其他服务。
2. 保留原资料库或一致性备份，先在副本演练。
3. 确认预览没有冲突，执行 `mpp storage migrate --apply`。
4. 中断后再次运行相同命令继续；任务服务会拒绝打开迁移未完成的库。
5. 核对归档、任务、正文、知识库检索、声纹与认证后，启动 daemon。

原地迁移逐目录移动。指定 `--target` 时复制到新库并保留来源；完成后由用户使用 `mpp config data_root <目标目录>` 切换当前资料库。切换配置与迁移是独立操作。复制期间不要编辑源库。

迁移先关闭进程内数据库，使用 SQLite backup API 备份已提交的 WAL 内容，检查数据库完整性、记录数及目录文件数/大小。完成清单保存在 `.mpp-layout-migration.json` 和 `backups/layout-*/migration.json`。JSON 路径与数据库副本一起更新。

副本仍含原库绝对路径时，增加 `--original-root <原始根目录>`，例如：

```powershell
mpp storage migrate --source D:/Video/Library-copy --original-root D:/Video/Library --apply
```

## 回滚

停止使用资料库的服务后，执行 `mpp storage migrate --rollback`。原地迁移恢复旧目录与迁移前数据库、JSON；已经完成迁移后产生的数据库和 JSON 修改保存在该批备份的 `before-rollback/`，可供人工取回。媒体文件随归档目录移回。

跨目录复制的回滚解除来源库的迁移状态，目标副本保留为 `incomplete_copy` 供检查；来源库仍可使用。再次复制应选择空目标目录。迁移记录与备份应保留到核对结束。

## 历史文件

旧 `history.json` 由历史 HistoryService 使用，当前任务 API 以 SQLite 为准。迁移保留该文件，不将其自动导入或覆盖现有任务。仓库 `data/tasks.db` 的历史空文件与当前 `data_root` 分开；调试数据库时应先通过 `get_workspace_paths().tasks_db` 确认位置。
