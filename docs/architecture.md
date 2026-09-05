# MediaProcessPipeline 架构

CLI（mpp）、网页和移动端通过 FastAPI daemon 使用媒体处理能力。daemon 固定端口 18000，同时提供 web/dist/ 静态网页。前端为 Vite、React 19 与 shadcn/ui，源码修改后在 web/ 运行 npm run build。

## 处理与调度

app.core.queue 管理下载、转写和后处理。下载使用可配置并发；GPU 阶段优先完成已就绪的转写，再处理 LLM 分析、润色、摘要和思维导图。重启依据已完成步骤恢复任务，切换阶段时释放对应 GPU 模型及本地 LLM 运行时。

app.core.pipeline 编排输入识别、媒体下载、音频准备、转写、LLM 后处理与归档。ASR、UVR、LLM 等服务通过 singleton getter 获取。ASR 按运行时配置选择后端；UVR 保留为可选的人声分离步骤。模型绑定与各阶段参数由运行时配置和 model router 决定。

## 存储契约

`app.core.settings` 保留运行时单例、原子保存、配置更新及公开类型导出。`app.core.configuration` 按职责组织字段模型（models）、旧自定义端点（profiles）、provider 与服务模型注册表（registry）、阶段模型绑定（bindings）、旧文档迁移与点路径更新（document），共享字段映射位于 constants。规范化函数仅处理传入的配置文档，保存成功后才更新运行时单例。

app.core.paths 统一解析配置与资料库路径。项目根目录 config.json 保存运行时设置，data_root 指向资料库。新库使用 archives/、state/、tmp/、logs/、backups/；现有混合目录按原布局继续运行。迁移由显式 CLI 命令执行，详见[资料库迁移与恢复](storage-migration.md)。

- TaskStore 在 SQLite 保存活动任务、历史、事件、文本产物和同步状态。重复保存任务保留关联记录。
- ArtifactStore 为受支持的正文和 JSON 写入提供文件与数据库副本更新入口；文件更新成功后同步 SQLite，并支持修复副本。
- 归档保留可读标题目录，archive_id 维持多端身份。任务和索引中的受管路径可相对资料库存储，API 解析为可用路径。
- 知识库使用 SQLite 与 sqlite-vec 保存文本分块及向量；声纹库保存人物、样本、任务说话人关联和音频片段。

ArchiveLifecycle 协调归档目录、任务、知识库、声纹关联与同步删除记录。删除先将目录移到受管临时位置，持久化进度后逐项完成；失败操作可重试。全局人物及声纹样本保留。

workspace_lifecycle 协调目录切换与文件/数据库操作。切换时检查活动任务和后台工作，重置存储连接；异步线程工作在取消和退出时等待真实线程结束后释放资源。配置采用临时文件与原子替换，保存失败保留此前运行设置。

## 接口与同步

HTTP 路由调用核心与服务模块。任务事件通过全局 /api/tasks/events 和单任务 /api/tasks/{id}/events SSE 推送；单任务 GET 查询保留兼容。

归档同步维护归档索引、revision、变更记录和删除记录。manifest 提供可传输文件清单；导入复用归档 ID。远程上传服务按配置发送已完成归档，并处理离线退避。完整性校验字段属于传输协议。

## 启动与开发

在 backend/ 运行 uv run python -m app.cli serve，或运行 uv run python run.py --reload。本地网页统一访问 http://localhost:18000。CLI 的离线设置和任务查询复用核心路径与存储逻辑。

业务回归测试位于 tests/，前端行为测试随组件存放。开发分析与实施证据位于 agentspace/。旧 HistoryService 作为历史文件兼容代码保留，当前任务查询以 SQLite 为准。
