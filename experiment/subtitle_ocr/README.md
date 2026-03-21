# 字幕 OCR 实验

通过视频帧分析 + VLM OCR 提取硬字幕（烧录在画面中的字幕）。

## 思路

1. **帧差分检测字幕切换** — ffmpeg 提取底部条带灰度帧，计算帧间像素差异，聚类出稳定字幕段
2. **VLM OCR** — 用 Qwen3.5-4B 对每段的代表帧做全帧 OCR，提取字幕文字
3. **输出 SRT** — 按时间段生成标准 SRT 字幕文件

## 实验结果

### 测试视频 1: GPT-SoVITS 推理引擎介绍（中文蓝色描边字幕）
- 前 120 秒，检测 25 段，21 段成功提取字幕
- 专有名词准确：`GENIE`、`High_Logic`、`200MB`（ASR 会误转为 Ginny、Hi Logic）
- 无字幕帧（录屏演示段）正确识别为 [无]，但 thinking 输出被误写入 SRT（需过滤）

### 测试视频 2: 斯坦福 CS336 详解模型推理（中英双语字幕）
- 前 120 秒，检测 37 段，36 段成功提取中英双语字幕
- 中英文都准确，格式为 `中文\n英文` 每行
- 直接提取了 UP 主烧录的翻译，质量高于 ASR+机翻

### 与 ASR 对比

| 方面 | OCR（硬字幕） | ASR（语音转录） |
|------|-------------|---------------|
| 专有名词 | 直接从画面读取，保留原始拼写 | 音译错误常见 |
| 多语言 | 能提取双语字幕 | 只转录语音语言 |
| 时间轴 | 以字幕切换为单位，段较完整 | 以语音停顿为单位，更细碎 |
| 无字幕段 | 正确识别，但需后处理过滤 | 始终有输出 |
| 速度 | ~15 秒/帧（thinking 模式） | 实时或更快 |

## 关键技术细节

### 模型加载
- **必须用 `AutoModelForImageTextToText`**，不是 `AutoModelForCausalLM`
- Qwen3.5-4B bf16，约 8.8GB，24G 显存够用
- transformers ≥ 5.3.0（支持 `qwen3_5` 架构）

### Step 1 参数
- 采样率：4fps
- 底部条带：画面中间 60% 宽度，底部 80px（跳过最底 15px 水印区）
- 变化阈值：mean pixel diff > 3.0
- 防抖：3 帧内的连续变化合并为一次
- 最短段：≥ 4 帧（1 秒）

### OCR Prompt
```
读出图片底部中央蓝色描边的字幕文字，只输出文字。没有字幕输出[无]
```
（对于中英双语字幕，改为要求格式 `中文\n英文`）

### 已知问题
- thinking 模式下 `max_new_tokens` 需要 ≥1024，否则推理被截断
- 无字幕帧的 thinking 过程偶尔被当作答案写入 SRT，需要后处理过滤
- Qwen3.5-4B 的 bbox grounding 不可靠（返回固定坐标），不推荐用于定位字幕区域

## 文件说明

| 文件 | 用途 |
|------|------|
| `step1_detect_segments.py` | 帧差分检测字幕时间段 |
| `step2_vlm_bbox.py` | VLM 定位字幕 bbox（实验性，效果不佳） |
| `step3_ocr_extract.py` | VLM OCR 提取字幕（早期版本） |
| `run_ocr.py` | GPT-SoVITS 视频 OCR 脚本 |
| `run_ocr_cs336.py` | CS336 视频 OCR 脚本（中英双语） |
| `out/sovits/` | GPT-SoVITS 实验输出 |
| `out/cs336/` | CS336 实验输出 |

## 模型

| 模型 | 路径 | 用途 |
|------|------|------|
| Qwen3.5-4B | `C:\zychen\AIGC\Models\Qwen3.5-4B` | OCR 推理（bf16, transformers） |
| Qwen3.5-9B-GGUF-Q8 | `C:\zychen\AIGC\Models\Qwen3.5-9B-GGUF-Q8` | 备用（未使用） |
