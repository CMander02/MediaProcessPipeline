# 思维导图 Map-Reduce 实验

## 背景

7小时访谈视频（谢赛宁）的思维导图生成质量极差——原 pipeline 用 DeepSeek 全文一次性生成，仅 68 行、5个一级节点，大量信息丢失。

## 问题根因

1. **全文超出有效上下文**：polished transcript ~153k chars ≈ ~102k tokens，DeepSeek 64-128k 上下文
2. **Prompt 过度压缩**："3-6个一级主题"、"每节点不超20字"、"最多3层"
3. **未利用 nfo 章节结构**：视频有14个官方章节标记（含时间戳），完全没用
4. **重新归类打乱时序**：访谈类内容的叙事线和因果关系丢失

## 实验方案：Map-Reduce

```
nfo 章节 (14个) → Map: 每章并行生成摘要 → Reduce: 分5组合并 → 最终输出
```

### Map 阶段
- 14个章节并行调用 DeepSeek
- 每章独立总结为结构化 markdown 列表
- 携带全局 metadata（标题、作者、章节列表）作为上下文
- 耗时 72 秒

### Reduce 阶段
- DeepSeek deepseek-chat 最大输出 **8K tokens**（不是之前以为的可调）
- 分5组（每组3-4章），各组独立 reduce，最后拼接
- 每组输出 2500-3900 tokens，全部 `finish=stop` 无截断
- 使用 openai SDK 直接调用（绕过 litellm 的路由问题）

## 实验结果

| 版本 | 行数 | 信息量 |
|------|------|--------|
| pipeline 现有 | 68 | 严重丢失 |
| map-reduce v1（单次合并） | 181 | 被 8K output 截断 |
| **map-reduce v2（分5组）** | **822** | **完整** |
| Claude Opus 人工整理 | ~350 | 完整（参考基准） |

## 已集成到 pipeline

commit `a940a0c`: `feat: map-reduce mindmap generation for long content`

改动文件：
- `backend/app/services/analysis/prompts/mindmap.py` — 新增 map/reduce prompt
- `backend/app/services/analysis/llm.py` — 新增 `_mindmap_map_reduce()` 方法族
- `backend/app/core/pipeline.py` — 传递 metadata（含 chapters）给 mindmap 生成

自动选择策略：
- < 15k chars → 单次生成
- ≥ 15k chars + 有 chapters → 按章节 map-reduce
- ≥ 15k chars + 无 chapters → 按段数自动切分 map-reduce

## DeepSeek API 注意事项

- `deepseek-chat` (V3.2): 128K 上下文，输出默认 4K 最大 **8K**
- `deepseek-reasoner` (V3.2 思考模式): 输出默认 32K 最大 64K
- 中文约 1.5 字/token
- reduce 阶段必须分组确保每组输出 < 8K tokens
