# 长文本与说话人处理

## 上下文预算

公共逻辑位于 `backend/app/services/analysis/long_text.py`。

- 预算按当前阶段的模型绑定读取：上下文容量减去输出预留、系统提示词和消息封装余量。
- UTF-8 字节数作为保守估算；服务端报告上下文不足或输出截断时继续缩小窗口。
- 摘要、分析、来源信息和导图使用 `generate_bounded`：重叠分块、逐块提炼，再递归合并。保留所有来源片段，包括结尾。
- 说话人推断、复核和字幕润色使用 `record_windows`。说话人步骤的编辑目标按原始字幕编号前进，邻接 Overlap 提供只读上下文。润色外层保留已有的重叠结果优选合并，内层按预算分窗。单条字幕过长时按正文拆分，再按任务语义合并。
- 姓名推断汇集全片证据；同一标签出现不同姓名，或同一姓名对应不同标签时保留匿名标签。超长字幕各片段的归属冲突时也保留不确定性。
- 很长的辅助简介和词表通过 `compact_metadata` 取首尾样本。完整转录正文继续逐窗处理；语言检测本身采用首、中、尾抽样。
- JSON 校验重试只带当前窗口和简短错误，避免把整段旧输出附加到请求后再次超限。
- 本地说话人步骤通过 llama.cpp 的 `response_format` 约束 JSON 语法，再由业务代码校验字幕编号、说话人和证据。接口格式见 [llama.cpp server 文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。

## 人数与身份

`task.options.num_speakers` 优先级最高。来源信息在 ASR 前准备，并在声学模型加载前释放本地 LLM。只有节目类型或单个嘉宾姓名时保留人数不确定性；上传账号不能直接作为人物姓名。

已知人数时，Pyannote 自动聚类产生的额外小簇通过 `speaker_clustering.py` 处理：用主要声学簇初始化归一化声纹的 K-means，再交给原有声学时间轴分配流程。这样减少通过强制切割聚类树而合并主要声音、保留离群声音的情况。未知人数继续采用声学模型的自动估计。

人数约束解决聚类数量；准确归属仍取决于音质、声纹、重叠语音与字幕时间轴。姓名需要来源或字幕证据。视频截图步骤尚未加入。

## 断点与排查

- `diarization.json` 保存人数参数与声学说话轮；人数参数或聚类版本变化会使旧缓存失效。
- `speaker_review.json` 保存每窗修改和失败窗口的原始编号；断点重跑会重新处理失败的复核并更新后续文本。
- `speaker_identity.json` 保存姓名对应的字幕证据与冲突标签。
- 主要回归测试：`test_llm_long_text.py`、`test_llm_structured_windows.py`、`test_speaker_identity.py`、`test_speaker_review.py`、`test_diarization_service.py`、`test_pipeline_stages.py`。
