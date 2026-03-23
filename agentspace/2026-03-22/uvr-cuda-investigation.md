# UVR 人声分离未使用 CUDA 问题调查

**日期**: 2026-03-22
**状态**: 待修复

## 现象

- 设置页面 `uvr_device` 配置为 `cuda`
- 实际运行时 CPU 占用 78%，GPU (NVIDIA) 仅 3%
- 处理速度 ~1.26s/it，符合 CPU 推理特征

## 根因

`backend/app/services/preprocessing/uvr.py` 的 `_ensure_init` 方法**完全没有读取 `uvr_device` 设置**。

- 读取了 `rt.uvr_model` 和 `rt.uvr_model_dir` ✓
- **从未读取 `rt.uvr_device`** ✗
- 创建 `Separator()` 实例时没有传入任何设备参数

### 对比其他服务（正确实现）

| 服务 | 代码 | 设备传递 |
|------|------|----------|
| WhisperX | `whisperx.py:81-86` | `device=rt.whisper_device` ✓ |
| Qwen3-ASR | `qwen3_asr.py:118` | `device_map=rt.qwen3_device` ✓ |
| UVR | `uvr.py:119,129,138` | 无设备参数 ✗ |

### audio-separator 库的设备检测

- `Separator.__init__()` 不直接暴露 `device` 参数
- 内部通过 `setup_accelerated_inferencing_device()` → `setup_torch_device()` 自动检测
- 自动检测逻辑：CUDA available → CUDA, else MPS → MPS, else CPU
- 理论上应自动检测到 CUDA，但实际表现为 CPU 运行，需进一步确认库 API

## 修复方向

1. 确认 `audio-separator` 库的 `Separator` 类如何接受设备参数（可能需要查看其构造函数签名或源码）
2. 在 `_ensure_init` 中读取 `rt.uvr_device` 并传入 `Separator()`
3. 如果库不支持直接传入设备，可能需要在初始化后手动 `.to(device)` 或设置环境变量
