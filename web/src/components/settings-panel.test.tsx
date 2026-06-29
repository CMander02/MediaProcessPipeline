/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SettingsPanel } from "./settings-panel"

const { mockSettings } = vi.hoisted(() => ({
  mockSettings: {
    llm_provider: "deepseek",
    asr_provider: "qwen3",
    qwen3_asr_model_path: "",
    qwen3_device: "cuda",
    local_llm_model_path: "",
    local_llm_n_gpu_layers: -1,
    local_llm_n_ctx: 16384,
    local_llm_n_batch: 512,
    polish_provider: "local",
    data_root: "D:/Video/MediaProcessPipeline",
    api_token: "",
    max_download_concurrency: 2,
    pipeline_overlap: true,
    generate_video_detail: true,
    custom_llm_profiles: [],
    custom_active_profile_id: "default",
    deepseek_api_base: "https://api.deepseek.com",
    deepseek_api_key: "",
    deepseek_analyze_model: "deepseek-v4-flash",
    deepseek_analyze_thinking: "disabled",
    deepseek_analyze_effort: "",
    deepseek_polish_model: "deepseek-v4-flash",
    deepseek_polish_thinking: "disabled",
    deepseek_polish_effort: "",
    deepseek_summary_model: "deepseek-v4-pro",
    deepseek_summary_thinking: "enabled",
    deepseek_summary_effort: "max",
    deepseek_mindmap_model: "deepseek-v4-flash",
    deepseek_mindmap_thinking: "disabled",
    deepseek_mindmap_effort: "",
    vlm_api_base: "",
    vlm_api_key: "",
    vlm_model: "qwen2.5-vl-7b-instruct",
    vlm_max_tokens: 1024,
    vlm_concurrency: 3,
    local_llm_device: "cuda",
    local_llm_dtype: "bfloat16",
    local_llm_max_new_tokens: 4096,
    uvr_model_dir: "",
    uvr_model: "UVR-MDX-NET-Inst_HQ_3",
    uvr_mdx_inst_hq3_path: "",
    uvr_device: "cuda",
    enable_voiceprint: true,
    voiceprint_match_threshold: 0.75,
    voiceprint_suggest_threshold: 0.6,
  },
}))

vi.mock("@/lib/api", () => ({
  api: {
    settings: {
      get: vi.fn().mockResolvedValue(mockSettings),
      patch: vi.fn().mockImplementation(async (updates: Record<string, unknown>) => ({
        ...mockSettings,
        ...updates,
      })),
      detectLocalUvr: vi.fn().mockResolvedValue({ found: false, path: "", models: [] }),
    },
    bilibili: {
      status: vi.fn().mockResolvedValue({ logged_in: false, message: "未登录" }),
    },
    platforms: {
      list: vi.fn().mockResolvedValue({
        platforms: [
          {
            id: "bilibili",
            preferred_quality: 64,
            prefer_subtitle: true,
            subtitle_engine: "native_wbi",
            subtitle_languages: "zh,en",
            subtitle_strict_validation: true,
            subtitle_min_coverage: 0.6,
            subtitle_allow_legacy_fallback: false,
          },
        ],
      }),
      update: vi.fn().mockResolvedValue({ ok: true }),
    },
  },
}))

vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
})

afterEach(() => {
  cleanup()
})

describe("SettingsPanel", () => {
  it("renders five hierarchical tabs", async () => {
    render(<SettingsPanel />)

    await screen.findByRole("button", { name: "Overall" })
    const nav = screen.getByRole("navigation")
    const labels = within(nav).getAllByRole("button").map((button) => button.textContent?.trim())

    expect(labels).toEqual([
      "Overall",
      "Knowledge Base",
      "LLM/API Registry",
      "Local Models",
      "Pipelines/Sources",
    ])
  })

  it("places platform source settings under Pipelines/Sources", async () => {
    render(<SettingsPanel />)

    fireEvent.click(await screen.findByRole("button", { name: "Pipelines/Sources" }))

    expect(await screen.findByText("哔哩哔哩")).toBeInTheDocument()
    expect(screen.getByText("YouTube")).toBeInTheDocument()
    expect(screen.getByText("小宇宙")).toBeInTheDocument()
    expect(screen.getByText("小红书")).toBeInTheDocument()
    expect(screen.getByText("知乎")).toBeInTheDocument()
  })

  it("shows registry and local models as searchable list-detail panels", async () => {
    render(<SettingsPanel />)

    fireEvent.click(await screen.findByRole("button", { name: "LLM/API Registry" }))

    expect(await screen.findByPlaceholderText("搜索模型服务...")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "DeepSeek" })).toBeInTheDocument()
    expect(screen.getByText("Vision Server")).toBeInTheDocument()
    expect(screen.getByText("Purpose Binding")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Vision Server"))
    expect(screen.getByRole("heading", { name: "Vision Server" })).toBeInTheDocument()
    expect(screen.getByDisplayValue("qwen2.5-vl-7b-instruct")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Local Models" }))

    expect(await screen.findByPlaceholderText("搜索本地模型...")).toBeInTheDocument()
    expect(await screen.findByRole("heading", { name: "Local LLM Server" })).toBeInTheDocument()
    expect(screen.getByText("UVR Server")).toBeInTheDocument()
    expect(screen.getByText("Voiceprint Purpose")).toBeInTheDocument()

    fireEvent.click(screen.getByText("UVR Server"))
    expect(screen.getByRole("heading", { name: "UVR Server" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "检查本机 UVR" })).toBeInTheDocument()
  })
})
