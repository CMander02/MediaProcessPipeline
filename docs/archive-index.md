# 归档列表与增量同步

文件页从 SQLite 归档索引分页读取。`GET /api/pipeline/archives?page=1&page_size=28` 返回 `archives`、`total`、实际 `page`、`page_size`、`workspace_id`、`revision`、`indexing` 和 `last_reconciled_at`。省略 `page` 保持旧接口的全量列表结构。

查询支持 `search`（标题包含）、`media`（all/video/audio/image）、`source` 和 `sort`（created_desc/created_asc/published_desc/title_asc）。处理中的归档优先，同值以 archive_id 稳定排序；页码超过末页时返回末页。

应用内的产物写入和任务状态变化标记对应归档，下次查询或同步时更新该归档的索引。删除保留 tombstone，历史 cursor 继续有效。同步传输校验字段保持兼容；变更检测直接比较文件路径、大小和修改时间以及索引内容。

外部编辑文件后，点击文件页“检查文件”，或调用 `POST /api/pipeline/archives/reconcile`。启动后台也会检查文件。`GET /api/pipeline/archives/index` 提供校验时间和当前状态。常规翻页及无变化同步查询无需扫描整个资料库；manifest 只读取目标归档。

索引可以从归档文件重建。外部工具若同时保持文件大小和修改时间，需强制重建以识别这种修改。Android 继续使用本地离线库完成筛选和分页。
