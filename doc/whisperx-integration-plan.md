# WhisperX 与 Audio-Separator 集成计划

> 临时保存的计划文档，待后续实施

## 核心建议：使用 uv 依赖管理，而非 Git Submodule

经过研究，**不建议使用 git submodule**，原因：
1. 两个库都是成熟的 PyPI 包，有复杂的依赖树（PyTorch、CUDA、ONNX 等）
2. uv 不能很好地处理 submodule 的复杂构建系统
3. Submodule 会增加维护负担而无明显收益

**推荐方案**：通过 uv optional dependencies 安装，配合现有的 **按需懒加载模式**（on-demand lazy loading）。

---

## 部署方式：按需加载 vs 常驻服务

| 因素 | 常驻服务 | 按需加载（推荐） |
|------|---------|----------------|
| 首次请求延迟 | ~100ms（热启动） | 10-30秒（冷加载） |
| 后续请求 | ~100ms | ~100ms（模型已缓存） |
| 空闲内存占用 | 4-8GB GPU | 0 |
| 进程管理 | 复杂（多进程） | 简单（单进程） |
| 开发体验 | 较难 | 简单 |

**结论**：现有代码已经实现了正确的按需加载模式，模型首次使用时加载，之后保持缓存。

---

## 实施步骤

### 1. 更新 pyproject.toml 依赖配置

```toml
[project.optional-dependencies]
dev = [...]
media = ["yt-dlp>=2024.0.0"]

# 新增：GPU 相关依赖
preprocessing = ["audio-separator[gpu]>=0.17.0"]
recognition = ["whisperx>=3.1.0"]
gpu = [
    "audio-separator[gpu]>=0.17.0",
    "whisperx>=3.1.0",
]
```

### 2. 创建 GPU 安装脚本 `scripts/install-gpu.ps1`

```powershell
# 安装 PyTorch with CUDA
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
# 安装 audio-separator 和 whisperx
uv pip install "audio-separator[gpu]" whisperx
```

### 3. 增强配置 `backend/app/core/config.py`

新增设置项：
- `model_cache_dir`: 模型缓存目录
- `whisperx_batch_size`: 批处理大小（默认 16）
- `max_concurrent_transcriptions`: 最大并发数（默认 1，GPU 内存限制）

### 4. 添加模型生命周期管理 `backend/app/services/model_manager.py`

提供显式的模型加载/卸载控制：
- `preload()`: 预加载模型（可选启动时调用）
- `unload()`: 卸载模型释放 GPU 内存
- `get_memory_usage()`: 查询 GPU 内存使用

### 5. 增强现有服务

**uvr.py 增强：**
- 添加 `asyncio.Lock` 线程安全
- 支持进度回调

**whisperx.py 增强：**
- 添加 `unload_models()` 方法释放内存
- 从配置读取 batch_size

### 6. 添加 API 端点

- `POST /api/pipeline/models/preload` - 预加载模型
- `POST /api/pipeline/models/unload` - 卸载模型
- `GET /api/pipeline/models/status` - 查询状态和内存

---

## 关键文件清单

| 文件 | 操作 |
|------|------|
| `pyproject.toml` | 修改 - 添加 optional dependencies |
| `scripts/install-gpu.ps1` | 新建 - GPU 安装脚本 |
| `backend/app/core/config.py` | 修改 - 添加新配置项 |
| `backend/.env.example` | 修改 - 添加新环境变量 |
| `backend/app/services/model_manager.py` | 新建 - 模型生命周期管理 |
| `backend/app/services/preprocessing/uvr.py` | 修改 - 添加线程安全 |
| `backend/app/services/recognition/whisperx.py` | 修改 - 添加内存管理 |
| `backend/app/api/routes/pipeline.py` | 修改 - 添加模型管理端点 |

---

## 验证方案

1. **安装验证**
   ```powershell
   .\scripts\install-gpu.ps1
   uv run python -c "import torch; print(torch.cuda.is_available())"
   uv run python -c "import whisperx; print('WhisperX OK')"
   uv run python -c "from audio_separator.separator import Separator; print('Audio-Separator OK')"
   ```

2. **API 验证**
   ```bash
   # 启动后端
   uv run uvicorn app.main:app --reload --port 8000

   # 测试模型状态
   curl http://localhost:8000/api/pipeline/models/status

   # 测试人声分离（需要测试音频文件）
   curl -X POST http://localhost:8000/api/pipeline/separate \
     -H "Content-Type: application/json" \
     -d '{"audio_path": "test.wav"}'

   # 测试转录
   curl -X POST http://localhost:8000/api/pipeline/transcribe \
     -H "Content-Type: application/json" \
     -d '{"audio_path": "test.wav"}'
   ```

3. **内存管理验证**
   ```bash
   # 预加载模型
   curl -X POST http://localhost:8000/api/pipeline/models/preload
   # 检查内存
   curl http://localhost:8000/api/pipeline/models/status
   # 卸载模型
   curl -X POST http://localhost:8000/api/pipeline/models/unload
   ```

---

## 技术参考

### WhisperX
- GitHub: https://github.com/m-bain/whisperX
- 安装: `pip install whisperx`
- 需要: CUDA 12.8, Python 3.9-3.13
- 性能: ~70x 实时（大模型 GPU）

### Audio-Separator
- GitHub: https://github.com/karaokenerds/python-audio-separator
- 安装: `pip install audio-separator[gpu]`
- 需要: CUDA 12.x + cuDNN 9.x
- 模型: UVR, MDX-Net, Demucs
