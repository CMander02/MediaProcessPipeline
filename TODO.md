1. 内容输出优化
   1. 字幕和源内容的语言保持一致
   2. 更好的导图生成
      1. 使用视频简介的时间轴划定结构
      2. 更好的导图内容
      3. 导图的结构在字幕中显示为浮动TOC？
      4. 导图的节点跳转到字幕位置？
   3. 更好的字幕对齐
      1. 字幕时间为主，说话人分割为辅，直接使用算法进行聚类，不用LLM进行聚类
2. 前端优化
   1. 更好的设置页面
   2. 更好的大量任务的处理交互逻辑
   3. 内容页数过多的时候，底部如何交互
   4. 更好的导图交互逻辑，仿照 notebooklm?
   5. 无文字的音频内容的封面处理？
3. 后端优化
   1. ASR 加速
   2. 能否并发说话人分离？
   3. LLM能否换用本地模型，比如满血Qwen3.5-4B，或者Qwen3.5-27B-Q4KM
   4. 各个流程如何协调
4. CLI
   1. 更好的 CLI  交互逻辑
   2. 更好的 CLI  信息可视化
5. 常见 bug
   1. UnicodeEncodeError: 'gbk' codec can't encode character '\U0001d404' in position 859: illegal multibyte sequence
6. 其他开发相关的
   1. 更好的前端组件设计风格，[shadcn 某个预设风格](https://ui.shadcn.com/create?preset=b3kIcnn6p)
   2. 视频有硬字幕的时候是否可以直接从视频里面提取字幕帧并OCR识别文字

7. Bilibili AI 字幕获取
   1. BBDown `--skip-ai false` 可以下载 Bilibili AI 字幕（通过 player API 的 AI-sub）
   2. 有 AI 字幕时可跳过 ASR，直接用平台字幕进入分析阶段，大幅加速初步处理
   3. 注意：AI 字幕质量参差不齐（有的只有 BGM 歌词），需要检测字幕是否有效
   4. 流程调整思路：有可用平台字幕 → 跳过 UVR/ASR → 直接 LLM 分析；无字幕或质量差 → 走完整 ASR 流程
8. 精细时轴合并（ASR + 平台字幕）
   1. 场景：用户勾选 force_asr 后，同时拥有 UVR+ASR 产出（精细时轴、粗糙文字）和平台AI字幕（精细文字、粗糙时轴）
   2. 目标：以 ASR 时轴为骨架，用平台字幕的文字内容替换/校准 ASR 的识别文字，产出"精细时轴+精细文字"的 SRT
   3. 可能的思路：基于时间戳对齐两组 segments，ASR segment 的时间范围内匹配最近的平台字幕文字；或用 DTW/编辑距离做序列对齐
   4. 依赖：字幕快速路径并行优化先完成
9. Qwen3-ASR VAD 改进
   1. 当前无 ForcedAligner 时用 Silero VAD 分段再逐段转录——多余且不匹配原生实现
   2. qwen_asr 包内部已有 energy-based split_audio_into_chunks（MAX_ASR_INPUT_SECONDS=1200s）
   3. 应直接调用 model.transcribe() 整段转录，由 qwen_asr 内部分块，去掉 Silero VAD 依赖
   4. 无 ForcedAligner 时没有时间戳——需要后处理方案（如用 VAD 对齐文本到时间段）

