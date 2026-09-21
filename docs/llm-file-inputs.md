# LLM 文本文件输入

管线先生成阶段请求，包含处理要求、原文和输出格式。输入方式随 Provider 的原生能力选择。

| Provider | 文本输入方式 |
| --- | --- |
| QoderCN | 完整请求写入 UTF-8 `request.txt`，短提示词指定当前目录中的文件；启用 `Read` 工具读取全文 |
| Kimi Code / Antigravity | 完整请求写入 UTF-8 `request.txt`，短提示词指定文件；长文件按顺序读取至末尾 |
| Codex | 完整请求写入 UTF-8 `request.txt`，通过 `codex exec -` 的标准输入传入文件内容；兼容 Windows 只读策略 |
| OpenAI 官方接口 | Responses API 的 `input_file`，携带 `request.txt` 文件名和 UTF-8 内容的 Base64 数据，配合短提示词 |
| Anthropic 官方接口 | Messages API 的原生 `document` 内容块，类型为 `text/plain`，配合短提示词 |
| DeepSeek / 自定义兼容接口 / 本地模型 | 沿用 HTTP 请求体或本地推理的文本输入 |

官方 API 的附件随推理请求发送，使用内联文件数据。系统提示词独立保留。OpenAI 和 Anthropic 使用自定义网关地址时沿用原有文本协议，便于兼容仅实现部分接口的网关。

CLI 的命令行参数保持短小，长原文保存在临时请求文件中。图片继续以附件或请求文件中列出的本地图片提供；完成、报错或取消后清理临时工作目录。

返回值仍是文本字符串，保持每个阶段原有格式，例如润色的 JSON、导图的 Markdown。文件输入中的文字仍计入模型上下文；分块大小、模型上下文和输出长度继续受各模型限制。

能力依据：

- [OpenAI File inputs](https://developers.openai.com/api/docs/guides/file-inputs)：Responses 支持文本文件和内联文件数据。
- [Anthropic document 输入](https://platform.claude.com/docs/en/build-with-claude/citations)：支持 `source.type=text`、`media_type=text/plain` 的原生文档块。
- [DeepSeek Files API](https://api-docs.deepseek.com/api/create-file/)：当前文件上传能力面向图片，文本请求通过 Chat Completions 发送。
- [Qoder CLI reference](https://docs.qoder.com/cli/cli-reference)：支持文件读取工具、权限范围和文本输出。
