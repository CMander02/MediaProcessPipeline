/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ComputeCapabilities, Settings } from "@/lib/api"
import { AudioFlowControls, AsrSettingsControls, LocalLlmSettingsControls, VocalSeparationControls } from "./audio-controls"
import { DeviceSettingRow } from "./device-setting-row"

const host: { capabilities: ComputeCapabilities | undefined; native: boolean; phone: boolean } = vi.hoisted(() => ({
  capabilities: undefined,
  native: false,
  phone: false,
}))

vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({ capabilities: { compute_capabilities: host.capabilities } }),
}))
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => ({ isNative: host.native }) }))
vi.mock("@/hooks/use-media-query", () => ({ useMediaQuery: () => host.phone }))

const cpuHost: ComputeCapabilities = {
  cuda_available: false,
  mps_available: false,
  metal_available: false,
  recommended_gpu_backend: null,
}

const settings: Settings = {
  llm_provider: "local", asr_provider: "llama_cpp", audio_processing_flow: "asr",
  sherpa_model_id: "", sherpa_model_root: "", sherpa_device: "auto", sherpa_num_threads: 4,
  sherpa_chunk_strategy: "vad", sherpa_max_chunk_sec: 30, sherpa_vad_model_path: "", sherpa_debug: false,
  asr_timestamp_mode: "auto", qwen3_aligner_model_path: "", llama_cpp_binary_path: "",
  uvr_model: "", uvr_device: "auto", uvr_model_dir: "", uvr_mdx_inst_hq3_path: "",
  uvr_hp_uvr_path: "", uvr_denoise_lite_path: "", uvr_kim_vocal_2_path: "",
  uvr_deecho_dereverb_path: "", uvr_htdemucs_path: "", uvr_chunk_duration_sec: 300,
  moss_cpp_binary_path: "", moss_cpp_model_path: "", moss_cpp_device: "auto", moss_cpp_threads: 8,
  moss_cpp_max_new_tokens: 8192, moss_cpp_chunk_duration_sec: 1200, moss_cpp_chunk_overlap_sec: 60,
  moss_cpp_timeout_sec: 3600, local_llm_model_path: "", local_llm_engine: "llama_cpp",
  local_llm_name: "Local LLM", local_llm_mmproj_path: "", local_llm_device: "auto",
  local_llm_dtype: "auto", local_llm_max_new_tokens: 4096, local_llm_thinking: false,
  local_llm_n_gpu_layers: -1, local_llm_n_ctx: 4096, local_llm_n_batch: 512,
  local_llm_timeout_sec: 300, local_llm_keepalive_sec: 600, local_llm_concurrency: 1,
  llm_polish_concurrency: 1, polish_provider: "local",
}

beforeEach(() => {
  host.capabilities = cpuHost
  host.native = false
  host.phone = false
  Element.prototype.scrollIntoView ??= () => {}
})
afterEach(cleanup)

function choose(label: string, option: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), { key: "ArrowDown" })
  fireEvent.click(screen.getByRole("option", { name: option }))
}

describe("local compute device settings", () => {
  it("shows CPU fallback and saves the unified GPU value on a CPU host", () => {
    const onChange = vi.fn()
    render(<DeviceSettingRow component="uvr" value="auto" onChange={onChange} />)
    expect(screen.getByText(/自动模式使用 CPU/)).toBeInTheDocument()
    choose("运行设备", "GPU（当前组件不可用）")
    expect(onChange).toHaveBeenCalledWith("gpu")
  })

  it.each(["cuda", "mps", "metal"])("displays the legacy %s value without changing the saved setting", (value) => {
    host.capabilities = { ...cpuHost, cuda_available: true, component_gpu_backends: { llama_cpp: "cuda" } }
    const onChange = vi.fn()
    render(<DeviceSettingRow component="llama_cpp" value={value} onChange={onChange} />)
    expect(screen.getByRole("combobox", { name: "运行设备" })).toHaveTextContent("GPU（CUDA）")
    expect(onChange).not.toHaveBeenCalled()
  })

  it.each([
    { component: "llama_cpp", backend: "metal", label: "Metal" },
    { component: "torch", backend: "mps", label: "MPS" },
    { component: "transformers", backend: "mps", label: "MPS" },
  ] satisfies Array<{ component: "llama_cpp" | "torch" | "transformers"; backend: "metal" | "mps"; label: string }>)(
    "shows $label for $component on Apple Silicon",
    ({ component, backend, label }) => {
      host.capabilities = {
        ...cpuHost, mps_available: true, metal_available: true,
        component_gpu_backends: { [component]: backend },
      }
      render(<DeviceSettingRow component={component} value="gpu" onChange={vi.fn()} />)
      expect(screen.getByRole("combobox", { name: "运行设备" })).toHaveTextContent(`GPU（${label}）`)
    },
  )

  it("uses component provider availability when CUDA hardware exists", () => {
    host.capabilities = { ...cpuHost, cuda_available: true, component_gpu_backends: { sherpa_onnx: null } }
    render(<DeviceSettingRow component="sherpa_onnx" value="gpu" onChange={vi.fn()} />)
    expect(screen.getByRole("combobox", { name: "运行设备" })).toHaveTextContent("GPU（当前组件不可用）")
    expect(screen.getByText(/自动模式使用 CPU/)).toBeInTheDocument()
  })

  it("gives each device selector an independent accessible label", () => {
    render(<>
      <DeviceSettingRow component="llama_cpp" value="auto" onChange={vi.fn()} />
      <DeviceSettingRow component="torch" value="cpu" onChange={vi.fn()} />
      <DeviceSettingRow component="uvr" value="gpu" onChange={vi.fn()} />
    </>)
    const selectors = screen.getAllByRole("combobox", { name: "运行设备" })
    expect(selectors).toHaveLength(3)
    expect(new Set(selectors.map((selector) => selector.id)).size).toBe(3)
    for (const selector of selectors) {
      expect(selector).toHaveAccessibleName("运行设备")
    }
  })

  it.each(["browser", "native"])("hides local compute controls on a %s phone", (kind) => {
    host.phone = kind === "browser"
    host.native = kind === "native"
    render(<DeviceSettingRow component="torch" value="auto" onChange={vi.fn()} />)
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("saves independent ForcedAligner and Pyannote devices", () => {
    const updateSetting = vi.fn().mockResolvedValue(undefined)
    render(<AsrSettingsControls settings={settings} updateSetting={updateSetting} saving={{}} saved={{}} sherpaStatus={null} />)
    choose("对齐设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("aligner_device", "cpu")
    choose("分离设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("pyannote_device", "cpu")
    choose("运行设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("llama_asr_device", "cpu")
  })

  it("saves Sherpa device independently of the alignment device", () => {
    const updateSetting = vi.fn().mockResolvedValue(undefined)
    render(<AsrSettingsControls settings={{ ...settings, asr_provider: "sherpa_onnx" }} updateSetting={updateSetting} saving={{}} saved={{}} sherpaStatus={null} />)
    choose("运行设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("sherpa_device", "cpu")
  })

  it("offers automatic CPU/GPU selection for UVR", () => {
    const updateSetting = vi.fn().mockResolvedValue(undefined)
    render(<VocalSeparationControls settings={settings} updateSetting={updateSetting} saving={{}} saved={{}} detectLocalUvr={vi.fn()} uvrDetecting={false} uvrDetection={null} />)
    expect(screen.getByRole("combobox", { name: "运行设备" })).toHaveTextContent("自动")
    choose("运行设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("uvr_device", "cpu")
  })

  it.each([
    { model: "1_HP-UVR", expected: "GPU（CUDA）" },
    { model: "htdemucs", expected: "GPU（CUDA）" },
    { model: "UVR-MDX-NET-Inst_HQ_3", expected: "GPU（当前组件不可用）" },
  ])("shows the selected $model runtime capability", ({ model, expected }) => {
    host.capabilities = {
      ...cpuHost,
      cuda_available: true,
      component_gpu_backends: { torch: "cuda", uvr: null },
    }
    render(<VocalSeparationControls settings={{ ...settings, uvr_model: model, uvr_device: "gpu" }} updateSetting={vi.fn()} saving={{}} saved={{}} detectLocalUvr={vi.fn()} uvrDetecting={false} uvrDetection={null} />)
    expect(screen.getByRole("combobox", { name: "运行设备" })).toHaveTextContent(expected)
  })

  it("saves MOSS device", () => {
    const updateSetting = vi.fn().mockResolvedValue(undefined)
    render(<AudioFlowControls settings={{ ...settings, audio_processing_flow: "moss" }} updateSetting={updateSetting} saving={{}} saved={{}} />)
    choose("运行设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("moss_cpp_device", "cpu")
  })

  it("saves the local LLM device", () => {
    const updateSetting = vi.fn().mockResolvedValue(undefined)
    render(<LocalLlmSettingsControls settings={settings} updateSetting={updateSetting} saving={{}} saved={{}} />)
    choose("运行设备", "CPU")
    expect(updateSetting).toHaveBeenLastCalledWith("local_llm_device", "cpu")
  })
})
