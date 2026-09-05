import { PathPickerRow, SettingRow, SettingsSection } from "../setting-controls"
import { Button } from "@/components/ui/button"
import { type LocalAsrModelsStatus } from "@/lib/api"
import { type SharedSettingsProps, type LocalModelSettingsProps } from "./types"
import { SelectSettingRow, SwitchSettingRow, AdvancedSettings, NumberSettingRow } from "./controls"
import { getRuntimeModelBindings } from "./registry-utils"

export function AudioFlowControls({
  settings,
  updateSetting,
  saving,
  saved,
}: SharedSettingsProps) {
  const flow = String(settings.audio_processing_flow ?? "asr")
  const diarizationEnabled = Boolean(settings.enable_diarization ?? true)

  return (
    <div>
      <SettingsSection
        title="处理方式"
        description="选择新任务默认使用的音频识别方式。"
      >
        <SelectSettingRow
          label="默认处理方式"
          value={flow}
          onChange={(value) => updateSetting("audio_processing_flow", value)}
        >
          <option value="asr">标准语音识别</option>
          <option value="moss">MOSS 一体化识别</option>
        </SelectSettingRow>
      </SettingsSection>

      {flow === "asr" ? (
        <SettingsSection
          title="说话人分离"
          description="为字幕片段添加 SPEAKER_XX 标签。模型路径统一在 ASR 页面配置。"
        >
          <SwitchSettingRow
            label="启用说话人分离"
            checked={diarizationEnabled}
            onChange={(value) => updateSetting("enable_diarization", value)}
          />
        </SettingsSection>
      ) : null}

      {flow === "moss" ? (
        <SettingsSection
          title="MOSS"
          description="MOSS 保留为独立本地识别方式。"
        >
          <PathPickerRow
            label="C++ 引擎"
            settingKey="moss_cpp_binary_path"
            value={String(settings.moss_cpp_binary_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="moss-transcribe.exe"
            title="选择 MOSS C++ 引擎"
            pickerLabel="选择文件"
          />
          <PathPickerRow
            label="GGUF 模型"
            settingKey="moss_cpp_model_path"
            value={String(settings.moss_cpp_model_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="moss-transcribe-q5_k.gguf"
            title="选择 MOSS GGUF 模型"
            pickerLabel="选择文件"
          />
          <SelectSettingRow
            label="运行设备"
            value={String(settings.moss_cpp_device ?? "auto")}
            onChange={(value) => updateSetting("moss_cpp_device", value)}
          >
            <option value="auto">自动</option>
            <option value="cuda">CUDA</option>
            <option value="cpu">CPU</option>
          </SelectSettingRow>
          <AdvancedSettings>
            <NumberSettingRow label="CPU 线程" settingKey="moss_cpp_threads" fallback={8} {...{ settings, updateSetting, saving, saved }} />
            <NumberSettingRow label="最大输出" settingKey="moss_cpp_max_new_tokens" fallback={8192} {...{ settings, updateSetting, saving, saved }} />
            <NumberSettingRow label="分段时长（秒）" settingKey="moss_cpp_chunk_duration_sec" fallback={1200} {...{ settings, updateSetting, saving, saved }} />
            <NumberSettingRow label="分段重叠（秒）" settingKey="moss_cpp_chunk_overlap_sec" fallback={60} {...{ settings, updateSetting, saving, saved }} />
            <NumberSettingRow label="超时（秒）" settingKey="moss_cpp_timeout_sec" fallback={14400} {...{ settings, updateSetting, saving, saved }} />
          </AdvancedSettings>
        </SettingsSection>
      ) : null}
    </div>
  )
}

export function VocalSeparationControls({
  settings,
  updateSetting,
  saving,
  saved,
  detectLocalUvr,
  uvrDetecting,
  uvrDetection,
}: LocalModelSettingsProps) {
  const modelPaths = [
    ["UVR-MDX-NET-Inst_HQ_3", "uvr_mdx_inst_hq3_path"],
    ["1_HP-UVR", "uvr_hp_uvr_path"],
    ["UVR-DeNoise-Lite", "uvr_denoise_lite_path"],
    ["Kim_Vocal_2", "uvr_kim_vocal_2_path"],
    ["UVR-DeEcho-DeReverb", "uvr_deecho_dereverb_path"],
    ["htdemucs", "uvr_htdemucs_path"],
  ] as const

  return (
    <div>
      <SettingsSection
        title="模型"
        description="选择 UVR 模型并设置统一模型目录。"
      >
        <SelectSettingRow
          label="默认模型"
          value={String(settings.uvr_model ?? "UVR-MDX-NET-Inst_HQ_3")}
          onChange={(value) => updateSetting("uvr_model", value)}
        >
          {modelPaths.map(([model]) => <option key={model} value={model}>{model}</option>)}
        </SelectSettingRow>
        <PathPickerRow
          label="模型目录"
          settingKey="uvr_model_dir"
          value={String(settings.uvr_model_dir ?? "")}
          onSave={updateSetting}
          saving={saving}
          saved={saved}
          placeholder="UVR models 目录；留空时自动扫描"
          title="选择 UVR 模型目录"
          pickerLabel="选择文件夹"
        />
        <div className="flex flex-wrap items-center gap-3 pl-[6.75rem]">
          <Button size="sm" variant="outline" onClick={detectLocalUvr} disabled={uvrDetecting} className="h-8">
            {uvrDetecting ? "检查中..." : "检查本机 UVR"}
          </Button>
          {uvrDetection ? <span className="text-xs text-muted-foreground">{uvrDetection}</span> : null}
        </div>
        <AdvancedSettings label="模型专用路径">
          {modelPaths.map(([model, settingKey]) => (
            <PathPickerRow
              key={settingKey}
              label={model}
              settingKey={settingKey}
              value={String(settings[settingKey] ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="可选：指定模型文件或目录"
              title={`选择 ${model} 模型`}
              pickerLabel="选择路径"
            />
          ))}
        </AdvancedSettings>
      </SettingsSection>

      <SettingsSection title="运行" description="配置人声分离使用的设备和长音频分段长度。">
        <SelectSettingRow
          label="运行设备"
          value={String(settings.uvr_device ?? "cuda")}
          onChange={(value) => updateSetting("uvr_device", value)}
        >
          <option value="cuda">CUDA</option>
          <option value="cpu">CPU</option>
        </SelectSettingRow>
        <NumberSettingRow label="分段时长（秒）" settingKey="uvr_chunk_duration_sec" fallback={300} {...{ settings, updateSetting, saving, saved }} />
      </SettingsSection>
    </div>
  )
}

export function AsrSettingsControls({
  settings,
  updateSetting,
  saving,
  saved,
  sherpaStatus,
}: SharedSettingsProps & { sherpaStatus: LocalAsrModelsStatus | null }) {
  const provider = String(settings.asr_provider ?? "sherpa_onnx")
  const installedCount = sherpaStatus?.models.filter((model) => model.installed && model.compatible).length ?? 0

  return (
    <div>
      <SettingsSection title="语音识别" description="配置默认 ASR 服务及本地 sherpa-onnx 模型。">
        <SelectSettingRow
          label="ASR 服务"
          value={provider}
          onChange={(value) => updateSetting("asr_provider", value)}
        >
          <option value="sherpa_onnx">Sherpa ONNX（本地）</option>
          <option value="siliconflow">SiliconFlow</option>
        </SelectSettingRow>

        {provider === "sherpa_onnx" ? (
          <>
            <SelectSettingRow
              label="默认模型"
              value={String(settings.sherpa_model_id ?? "sensevoice-small-int8")}
              onChange={(value) => updateSetting("sherpa_model_id", value)}
            >
              <option value="qwen3-asr-1.7b-onnx">Qwen3-ASR 1.7B INT8</option>
              <option value="sensevoice-small-int8">SenseVoice Small INT8</option>
              <option value="paraformer-zh-int8">Paraformer Chinese INT8</option>
              <option value="whisper-small-multi-int8">Whisper Small Multilingual INT8</option>
            </SelectSettingRow>
            <div className="space-y-1 pl-[6.75rem] text-xs text-muted-foreground">
              <p>{sherpaStatus ? `本地模型 ${installedCount}/${sherpaStatus.models.length} 可用` : "正在读取本地模型状态"}</p>
              {sherpaStatus?.models.map((model) => (
                <p key={model.id} className="flex items-center gap-2">
                  <span className={model.installed && model.compatible ? "text-emerald-600" : "text-muted-foreground"}>
                    {model.installed && model.compatible ? "●" : "○"}
                  </span>
                  <span>{model.display_name}</span>
                  <span>{model.installed && model.compatible ? "可用" : model.error || "待安装"}</span>
                </p>
              ))}
            </div>
            <PathPickerRow
              label="模型根目录"
              settingKey="sherpa_model_root"
              value={String(settings.sherpa_model_root ?? sherpaStatus?.model_root ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="sherpa-onnx 模型根目录"
              title="选择 sherpa-onnx 模型根目录"
              pickerLabel="选择文件夹"
            />
            <SelectSettingRow
              label="运行设备"
              value={String(settings.sherpa_device ?? "auto")}
              onChange={(value) => updateSetting("sherpa_device", value)}
            >
              <option value="auto">自动</option>
              <option value="cuda">CUDA</option>
              <option value="cpu">CPU</option>
            </SelectSettingRow>
          </>
        ) : (
          <SettingRow
            label="模型"
            settingKey="siliconflow_asr_model"
            value={String(settings.siliconflow_asr_model ?? "FunAudioLLM/SenseVoiceSmall")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
        )}
      </SettingsSection>

      {provider === "sherpa_onnx" ? (
        <>
          <SettingsSection title="时间戳" description="选择字幕时间戳来源。Qwen3 ForcedAligner 仅在强制对齐模式下加载。">
            <SelectSettingRow
              label="时间戳模式"
              value={String(settings.asr_timestamp_mode ?? "auto")}
              onChange={(value) => updateSetting("asr_timestamp_mode", value)}
            >
              <option value="auto">自动</option>
              <option value="native">模型原生时间戳</option>
              <option value="vad">VAD 分段时间戳</option>
              <option value="qwen_forced">Qwen3 ForcedAligner</option>
            </SelectSettingRow>
            <PathPickerRow
              label="VAD 模型"
              settingKey="sherpa_vad_model_path"
              value={String(settings.sherpa_vad_model_path ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="silero_vad.onnx"
              title="选择 Silero VAD ONNX 模型"
              pickerLabel="选择文件"
            />
            <PathPickerRow
              label="对齐模型"
              settingKey="qwen3_aligner_model_path"
              value={String(settings.qwen3_aligner_model_path ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="Qwen3-ForcedAligner-0.6B 本地目录"
              title="选择 Qwen3 ForcedAligner 模型目录"
              pickerLabel="选择文件夹"
            />
            <AdvancedSettings>
              <SelectSettingRow
                label="切分策略"
                value={String(settings.sherpa_chunk_strategy ?? "vad")}
                onChange={(value) => updateSetting("sherpa_chunk_strategy", value)}
              >
                <option value="vad">VAD</option>
                <option value="fixed">固定时长</option>
              </SelectSettingRow>
              <NumberSettingRow label="CPU 线程" settingKey="sherpa_num_threads" fallback={4} {...{ settings, updateSetting, saving, saved }} />
              <NumberSettingRow label="最大分段（秒）" settingKey="sherpa_max_chunk_sec" fallback={30} {...{ settings, updateSetting, saving, saved }} />
              <SwitchSettingRow
                label="调试日志"
                checked={Boolean(settings.sherpa_debug ?? false)}
                onChange={(value) => updateSetting("sherpa_debug", value)}
              />
            </AdvancedSettings>
          </SettingsSection>

          <SettingsSection title="说话人模型" description="配置 pyannote 本地模型。开关位于音频流程页面。">
            <PathPickerRow
              label="Diarization"
              settingKey="pyannote_model_path"
              value={String(settings.pyannote_model_path ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="pyannote-speaker-diarization-3.1 本地目录"
              title="选择 pyannote diarization 模型目录"
              pickerLabel="选择文件夹"
            />
            <PathPickerRow
              label="Segmentation"
              settingKey="pyannote_segmentation_path"
              value={String(settings.pyannote_segmentation_path ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="pyannote-segmentation-3.0 本地目录"
              title="选择 pyannote segmentation 模型目录"
              pickerLabel="选择文件夹"
            />
            <PathPickerRow
              label="Embedding"
              settingKey="pyannote_embedding_path"
              value={String(settings.pyannote_embedding_path ?? "")}
              onSave={updateSetting}
              saving={saving}
              saved={saved}
              placeholder="pyannote_wespeaker-voxceleb-resnet34-LM 本地目录"
              title="选择 pyannote embedding 模型目录"
              pickerLabel="选择文件夹"
            />
            <AdvancedSettings>
              <SettingRow label="HF Proxy" settingKey="hf_proxy" value={String(settings.hf_proxy ?? "")} onSave={updateSetting} saving={saving} saved={saved} masked placeholder="留空时使用系统代理" />
              <SettingRow label="HF Token" settingKey="hf_token" value={String(settings.hf_token ?? "")} onSave={updateSetting} saving={saving} saved={saved} masked />
              <NumberSettingRow label="分离批量" settingKey="diarization_batch_size" fallback={16} {...{ settings, updateSetting, saving, saved }} />
            </AdvancedSettings>
          </SettingsSection>
        </>
      ) : null}
    </div>
  )
}

export function LocalLlmSettingsControls({ settings, updateSetting, saving, saved }: SharedSettingsProps) {
  const engine = String(settings.local_llm_engine ?? "transformers")

  return (
    <div>
      <SettingsSection title="运行时" description="选择本地 LLM 推理引擎、显示名称和运行设备。">
        <SelectSettingRow label="推理引擎" value={engine} onChange={(value) => updateSetting("local_llm_engine", value)}>
          <option value="llama_cpp">llama.cpp · GGUF</option>
          <option value="transformers">Transformers · Hugging Face</option>
        </SelectSettingRow>
        <SettingRow label="名称" settingKey="local_llm_name" value={String(settings.local_llm_name ?? "Local LLM")} onSave={updateSetting} saving={saving} saved={saved} />
        <SelectSettingRow label="运行设备" value={String(settings.local_llm_device ?? "auto")} onChange={(value) => updateSetting("local_llm_device", value)}>
          <option value="auto">自动</option>
          <option value="cuda">CUDA</option>
          <option value="cpu">CPU</option>
        </SelectSettingRow>
        {engine === "llama_cpp" ? (
          <div className="pl-[6.75rem]">
            <Button
              size="sm"
              variant="outline"
              onClick={() => updateSetting("runtime_model_bindings", {
                ...getRuntimeModelBindings(settings),
                vision: {
                  provider_id: "local",
                  model_id: String(settings.local_llm_name || "Local LLM"),
                  capability: "vision",
                },
              })}
              className="h-8"
            >
              设为图文理解
            </Button>
          </div>
        ) : null}
      </SettingsSection>

      <SettingsSection title="模型文件" description="使用完整路径配置推理程序、模型和多模态 projector。">
        {engine === "llama_cpp" ? (
          <PathPickerRow label="llama.cpp" settingKey="llama_cpp_binary_path" value={String(settings.llama_cpp_binary_path ?? "")} onSave={updateSetting} saving={saving} saved={saved} placeholder="llama-server.exe" title="选择 llama.cpp 可执行文件" pickerLabel="选择文件" />
        ) : null}
        <PathPickerRow
          label="模型路径"
          settingKey="local_llm_model_path"
          value={String(settings.local_llm_model_path ?? "")}
          onSave={updateSetting}
          saving={saving}
          saved={saved}
          placeholder={engine === "llama_cpp" ? "GGUF 模型文件" : "Hugging Face 模型目录"}
          title={engine === "llama_cpp" ? "选择 GGUF 模型" : "选择 Hugging Face 模型目录"}
          pickerLabel={engine === "llama_cpp" ? "选择文件" : "选择文件夹"}
        />
        {engine === "llama_cpp" ? (
          <PathPickerRow label="mmproj" settingKey="local_llm_mmproj_path" value={String(settings.local_llm_mmproj_path ?? "")} onSave={updateSetting} saving={saving} saved={saved} placeholder="多模态 projector GGUF" title="选择多模态 projector" pickerLabel="选择文件" />
        ) : (
          <SelectSettingRow label="数据类型" value={String(settings.local_llm_dtype ?? "bfloat16")} onChange={(value) => updateSetting("local_llm_dtype", value)}>
            <option value="auto">自动</option>
            <option value="bfloat16">bfloat16</option>
            <option value="float16">float16</option>
            <option value="float32">float32</option>
          </SelectSettingRow>
        )}
      </SettingsSection>

      <SettingsSection title="高级设置" description="控制上下文、生成长度和模型进程保活时间。">
        <NumberSettingRow label="Context" settingKey="local_llm_n_ctx" fallback={16384} {...{ settings, updateSetting, saving, saved }} />
        {engine === "llama_cpp" ? <NumberSettingRow label="GPU 层" settingKey="local_llm_n_gpu_layers" fallback={-1} {...{ settings, updateSetting, saving, saved }} /> : null}
        {engine === "llama_cpp" ? <NumberSettingRow label="Batch" settingKey="local_llm_n_batch" fallback={512} {...{ settings, updateSetting, saving, saved }} /> : null}
        <NumberSettingRow label="并发" settingKey="local_llm_concurrency" fallback={2} {...{ settings, updateSetting, saving, saved }} />
        <NumberSettingRow label="最大输出" settingKey="local_llm_max_new_tokens" fallback={4096} {...{ settings, updateSetting, saving, saved }} />
        <NumberSettingRow label="超时（秒）" settingKey="local_llm_timeout_sec" fallback={300} {...{ settings, updateSetting, saving, saved }} />
        <NumberSettingRow label="保活（秒）" settingKey="local_llm_keepalive_sec" fallback={600} {...{ settings, updateSetting, saving, saved }} />
      </SettingsSection>
    </div>
  )
}
