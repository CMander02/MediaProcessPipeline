# 音频输入→文本输出模型调研

日期: 2026-03-21

## 一、开源 ASR 模型（纯语音转文字）

### Whisper 家族 (OpenAI, MIT)

| 模型 | 参数量 | VRAM | WER | 语言 | 特点 |
|------|--------|------|-----|------|------|
| Whisper Large V3 | 1.55B | ~10GB | 7.4% | 99+ | 多语言基线，68万小时训练数据 |
| Whisper Large V3 Turbo | 809M | ~6GB | 7.75% | 99+ | 解码器32→4层，比V3快6x |
| Distil-Whisper Large V3 | 756M | ~5GB | 接近V3 | 仅英语 | 蒸馏版，快6.3x |
| WhisperX (管线) | - | - | - | 取决于底层 | wav2vec2对齐 + pyannote说话人分离 + VAD分段 |

### NVIDIA NeMo

| 模型 | 参数量 | WER | 语言 | 特点 |
|------|--------|-----|------|------|
| Parakeet TDT 1.1B v3 | 1.1B | 8.0% | 英语+25欧洲语言 | RTFx>2000，推理吞吐最快 |
| Canary Qwen 2.5B | 2.5B | **5.63%** | 英语为主 | Open ASR排行榜第一，FastConformer+Qwen3-1.7B |

### Qwen3-ASR (阿里, Apache 2.0)

| 模型 | 参数量 | 语言 | 特点 |
|------|--------|------|------|
| Qwen3-ASR-1.7B | 1.7B | 30语言+22中文方言 | 中文方言SOTA，流式/离线统一 |
| Qwen3-ASR-0.6B | 0.6B | 同上 | 128并发下2000x吞吐 |

### 中文专用

| 模型 | 提供方 | 参数量 | CER | 特点 |
|------|--------|--------|-----|------|
| FireRedASR-LLM | FireRed | 8.3B | **3.05%** | 中文SOTA，支持歌词识别 |
| FireRedASR-AED | FireRed | 1.1B | 3.18% | 轻量版，可作LLM语音编码器 |
| GLM-ASR-Nano-2512 | Z.AI | 1.5B | 4.10% | 粤语/方言专精，低音量场景强 |

### 其他

| 模型 | 提供方 | 参数量 | 特点 |
|------|--------|--------|------|
| IBM Granite Speech 3.3 8B | IBM | ~9B | WER 5.85%，抗噪强，Apache 2.0 |
| Microsoft Phi-4 Multimodal | Microsoft | ~5.6B | WER 6.14%，同时处理文本/图像/音频，MIT |
| Meta Omnilingual ASR | Meta | 300M-7B | 1600+语言，零样本扩展到5400+语言 |
| Moonshine v2 | Useful Sensors | 27M起 | 边缘/IoT部署，滑动窗口流式 |

## 二、多模态 LLM（音频理解，不仅是转录）

### 闭源 API

| 模型 | 提供方 | 特点 |
|------|--------|------|
| GPT-4o Audio | OpenAI | 原生音频处理，128K上下文，<320ms延迟，说话人标注 |
| gpt-4o-transcribe | OpenAI | 专用转录端点，幻觉减少89% |
| Gemini 2.5 Flash/Pro | Google | 原生音频token，支持1小时+长音频，Live API流式 |

### 开源/开放权重

| 模型 | 提供方 | 参数量 | 语言 | 特点 |
|------|--------|--------|------|------|
| Qwen3-Omni | 阿里 | 30B-A3B (MoE) | 119文字/19语音 | 40分钟+音频理解，32/36音频基准SOTA |
| Qwen2-Audio-7B | 阿里 | 8.2B | 中英为主 | 语音情感识别、音频QA、语音聊天 |
| Kimi-Audio-7B | 月之暗面 | 7B | 中英 | 1300万小时训练，最全面的开源音频模型 |
| SALMONN 2+ 72B | 字节/清华 | 72B | 多语言 | 音视频理解，超越GPT-4o |
| Audio Flamingo 3 | NVIDIA | 7B | 多语言 | 5000万音频-文本对，音乐+声音+语音理解 |
| MiniCPM-o | OpenBMB | 9B | 多语言 | 全双工多模态，可移动端部署 |

## 三、云端 API 服务

| 服务 | 提供商 | 语言数 | 特点 | 价格 |
|------|--------|--------|------|------|
| Whisper API | OpenAI | 100+ | 批量+实时，说话人分离 | $0.006/min |
| Nova-3 | Deepgram | 40+ | <300ms延迟，实时code-switch | ~$0.0043/min |
| Universal-2 | AssemblyAI | 99 | 说话人分离，LeMUR查询 | $0.37/hr |
| Speech-to-Text v2 | Google | 125+ | Chirp 3，流式+批量 | 按量付费 |
| Azure Speech | Microsoft | 140+ | 自定义模型，企业集成 | 按量付费 |
| Solaria-1 | Gladia | 100+ | 103ms部分延迟，NER+情感分析+翻译打包 | $0.55/hr |

## 四、对本项目的建议

当前环境：Windows + 4090 (CUDA)，主要处理中英文媒体。

| 场景 | 推荐 | 理由 |
|------|------|------|
| 中文准确度（本地） | Qwen3-ASR-1.7B（已集成）或 FireRedASR-LLM | Qwen3已有，FireRed CER更低但更重 |
| 中文方言/粤语 | Qwen3-ASR（22种方言）或 GLM-ASR-Nano | Qwen3方言覆盖最广 |
| 多语言均衡（本地） | Whisper Large V3 Turbo | 99语言，MIT，~6GB VRAM |
| 超越转录的音频理解 | Kimi-Audio-7B 或 Qwen3-Omni | Kimi更轻，Qwen3-Omni更强 |
| API 方案 | gpt-4o-transcribe（质量）/ Deepgram Nova-3（速度/成本） | |
| 说话人分离（本地） | WhisperX + pyannote-audio v4 | 已集成 |

### 值得关注的趋势

1. **ASR 和 LLM 融合**：Canary Qwen、Granite Speech、FireRedASR-LLM 都用 LLM 作为解码器
2. **中文方言覆盖**：Qwen3-ASR 的 22 种方言支持是独有优势
3. **端侧部署**：Moonshine v2、MiniCPM-o 推进移动端/边缘场景
4. **音频理解 > 转录**：Kimi-Audio、Qwen3-Omni 等模型不再只是 ASR，而是真正的音频理解

## 参考来源

- [Open ASR Leaderboard (HuggingFace)](https://huggingface.co/blog/open-asr-leaderboard)
- [Best open source STT model in 2026 (Northflank)](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Qwen3-ASR Technical Report](https://arxiv.org/abs/2601.21337)
- [FireRedASR](https://github.com/FireRedTeam/FireRedASR)
- [Kimi-Audio](https://github.com/MoonshotAI/Kimi-Audio)
- [Meta Omnilingual ASR](https://github.com/facebookresearch/omnilingual-asr)
- [IBM Granite Speech](https://huggingface.co/ibm-granite/granite-speech-3.3-8b)
- [Microsoft Phi-4 Multimodal](https://huggingface.co/microsoft/Phi-4-multimodal-instruct)
