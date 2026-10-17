# 长内容的正文选择与主题组织

总结流程先识别开场预告，再提取章节内容、组织全局主题，最后生成整期摘要。

- 正文选择使用开场字幕或段落、元数据，以及后文的相关片段作为证据。模型返回正文起点和引用 ID，程序只允许移除连续开场前缀。原始字幕保持完整，选择结果写入 `content_selection.json`；识别失败时保留全文。
- 来源没有结构化 chapters 时，从简介中的独立时间轴行提取章节。原生 chapters 优先。来源时间轴用于定位，主题父节点用于阅读。
- 章节提取仅使用该章节原文。要点包含原文引用和 source ID；程序校验引用并计算时间戳。短章节可以只产生一个要点，文章段落保持无时间戳。修复后仍无效的要点回退到其引用的原文摘录，有效要点继续保留。
- 全局组织覆盖全部章节，并保持原文顺序。模型按问题、论证或叙事目的划分连续章节区间，再复核边界。程序保留原子树，添加主题父节点，检查遗漏与重复。短小导图直接保留，分类失败保留提取结果。
- 摘要读取筛选后的原文，同时参考已组织的主题。章节摘要从对应导图节点附回来源时间轴，避免每个压缩批次反复生成整条时间轴。长文本中间阶段只提取简短事实，最终一步生成完整摘要 JSON。访谈建议需要保留提出者、条件和范围。

普通处理和字幕快速路径共用正文选择及主题上下文逻辑。预告结束落在来源章节内部时，保留实际主持人开场，并以其原始字幕时间建立“正文开场”节点。

## 本地评估

评估脚本通过已注册 provider 调用模型，结果写入指定输出目录。`calls.json` 包含原文提示和响应，适合本地检查；分享前应检查内容。

```powershell
.venv\Scripts\python.exe scripts/evaluate_summary_structure.py `
  --archive '<archive-directory>' --provider local `
  --mode full --output tmp/summary-evaluation/local

.venv\Scripts\python.exe scripts/evaluate_summary_structure.py `
  --archive '<archive-directory>' --provider codex-oauth `
  --mode full --output tmp/summary-evaluation/luna
```

`--mode selection` 测正文选择；`--mode structure` 在同一份已有导图上比较归类。`--reuse-outline` 可复用提取结果，应在报告中明确区分共享提取与完整独立处理。记录实际 provider、model、请求耗时及回退状态；synthetic case 只用于检查行为，不能代表真实内容上的总体准确率。
