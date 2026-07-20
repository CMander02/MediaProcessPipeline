/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SettingsPanel } from "./settings-panel"
import { api } from "@/lib/api"

const { mockSettings } = vi.hoisted(() => ({
  mockSettings: {
    llm_provider: "deepseek",
    asr_provider: "qwen3_gguf",
    audio_processing_flow: "asr",
    qwen3_asr_model_path: "",
    qwen3_device: "cuda",
    llama_cpp_binary_path: "",
    qwen3_gguf_model_path: "",
    qwen3_gguf_mmproj_path: "",
    qwen3_gguf_hf_repo: "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0",
    qwen3_gguf_device: "auto",
    qwen3_gguf_ctx: 4096,
    qwen3_gguf_n_gpu_layers: 99,
    qwen3_gguf_timeout_sec: 300,
    qwen3_gguf_keepalive_sec: 300,
    qwen3_gguf_chunk_strategy: "silero_onnx",
    silero_onnx_model_path: "",
    local_llm_model_path: "",
    local_llm_n_gpu_layers: -1,
    local_llm_n_ctx: 16384,
    local_llm_n_batch: 512,
    polish_provider: "local",
    data_root: "D:/Video/MediaProcessPipeline",
    api_token: "",
    ytdlp_auto_update: false,
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
    vlm_concurrency: 1,
    vlm_timeout_sec: 180,
    kb_embedding_model: "qwen3-embedding-0.6b",
    jina_reader_enabled: true,
    jina_reader_api_base: "https://r.jina.ai",
    jina_reader_api_key: "",
    jina_reader_bypass_cache: false,
    web_scrape_timeout_sec: 30,
    siliconflow_api_base: "https://api.siliconflow.cn/v1",
    siliconflow_api_key: "",
    siliconflow_asr_model: "FunAudioLLM/SenseVoiceSmall",
    service_models: [
      {
        id: "siliconflow-asr:baai-bge-m3",
        connection_id: "siliconflow-asr",
        model_id: "BAAI/bge-m3",
        display_name: "BAAI/bge-m3",
        model_type: "embedding",
        capabilities: ["embedding"],
        endpoint_path: "/embeddings",
        enabled: true,
      },
      {
        id: "siliconflow-asr:baai-bge-reranker-v2-m3",
        connection_id: "siliconflow-asr",
        model_id: "BAAI/bge-reranker-v2-m3",
        display_name: "BAAI/bge-reranker-v2-m3",
        model_type: "rerank",
        capabilities: ["rerank"],
        endpoint_path: "/rerank",
        enabled: true,
      },
      {
        id: "siliconflow-asr:pro-deepseek-ai-deepseek-v3.2",
        connection_id: "siliconflow-asr",
        model_id: "Pro/deepseek-ai/DeepSeek-V3.2",
        display_name: "Pro/deepseek-ai/DeepSeek-V3.2",
        model_type: "vlm",
        capabilities: ["chat", "vision"],
        endpoint_path: "/chat/completions",
        enabled: true,
      },
    ],
    runtime_model_bindings: {
      embedding: {
        provider_id: "custom-embedding-default",
        model_id: "qwen3-embedding-0.6b",
        capability: "embedding",
      },
    },
    local_llm_device: "cuda",
    local_llm_dtype: "bfloat16",
    local_llm_max_new_tokens: 4096,
    uvr_model_dir: "",
    uvr_model: "UVR-MDX-NET-Inst_HQ_3",
    uvr_mdx_inst_hq3_path: "",
    uvr_device: "cuda",
    moss_cpp_binary_path: "",
    moss_cpp_model_path: "",
    moss_cpp_device: "auto",
    moss_cpp_threads: 8,
    moss_cpp_max_new_tokens: 32768,
    moss_cpp_timeout_sec: 3600,
  },
}))

vi.mock("@/lib/api", () => ({
  getApiToken: vi.fn(() => ""),
  persistApiToken: vi.fn(),
  api: {
    settings: {
      get: vi.fn().mockResolvedValue(mockSettings),
      patch: vi.fn().mockImplementation(async (updates: Record<string, unknown>) => ({
        ...mockSettings,
        ...updates,
      })),
      detectLocalUvr: vi.fn().mockResolvedValue({ found: false, path: "", models: [] }),
      fetchSiliconFlowModels: vi.fn().mockResolvedValue({
        models: [
          { id: "Qwen/Qwen3.5-8B", display_name: "Qwen/Qwen3.5-8B", model_type: "llm" },
        ],
      }),
      fetchProviderModels: vi.fn().mockResolvedValue({
        provider_id: "siliconflow",
        source: "remote",
        models: [
          {
            id: "siliconflow:Qwen/Qwen3.5-8B",
            model_id: "Qwen/Qwen3.5-8B",
            display_name: "Qwen/Qwen3.5-8B",
            model_type: "llm",
            capabilities: ["llm", "chat"],
            endpoint_path: "/chat/completions",
            enabled: true,
            default_params: {},
          },
          {
            id: "siliconflow:BAAI/bge-m3",
            model_id: "BAAI/bge-m3",
            display_name: "BAAI/bge-m3",
            model_type: "embedding",
            capabilities: ["embedding"],
            endpoint_path: "/embeddings",
            enabled: true,
            default_params: {},
          },
        ],
        configured_models: [
          {
            id: "siliconflow:BAAI/bge-m3",
            model_id: "BAAI/bge-m3",
            display_name: "BAAI/bge-m3",
            model_type: "embedding",
            capabilities: ["embedding"],
            endpoint_path: "/embeddings",
            enabled: true,
            default_params: {},
          },
        ],
        allowed_models: [
          {
            id: "siliconflow:BAAI/bge-m3",
            model_id: "BAAI/bge-m3",
            display_name: "BAAI/bge-m3",
            model_type: "embedding",
            capabilities: ["embedding"],
            endpoint_path: "/embeddings",
            enabled: true,
            default_params: {},
          },
        ],
        error: null,
      }),
      syncProviderModels: vi.fn().mockResolvedValue({
        provider: {
          id: "siliconflow",
          name: "SiliconFlow",
          provider_type: "siliconflow",
          enabled: true,
          api_base: "https://api.siliconflow.cn/v1",
          api_key: "",
          models: [
            {
              id: "siliconflow:Qwen/Qwen3.5-8B",
              model_id: "Qwen/Qwen3.5-8B",
              display_name: "Qwen/Qwen3.5-8B",
              model_type: "llm",
              capabilities: ["llm", "chat"],
              endpoint_path: "/chat/completions",
              enabled: true,
              default_params: {},
            },
          ],
        },
        models: [
          {
            id: "siliconflow:Qwen/Qwen3.5-8B",
            model_id: "Qwen/Qwen3.5-8B",
            display_name: "Qwen/Qwen3.5-8B",
            model_type: "llm",
            capabilities: ["llm", "chat"],
            endpoint_path: "/chat/completions",
            enabled: true,
            default_params: {},
          },
        ],
      }),
      inferProviderModelMetadata: vi.fn().mockImplementation(async ({ model_id }: { model_id: string }) => ({
        id: `siliconflow:${model_id}`,
        model_id,
        display_name: model_id,
        model_type: "llm",
        capabilities: ["llm", "chat"],
        endpoint_path: "/chat/completions",
        enabled: true,
        default_params: {},
      })),
      queryProviderBalance: vi.fn().mockResolvedValue({ provider_id: "siliconflow", balance: {} }),
      ytdlpStatus: vi.fn().mockResolvedValue({
        installed: "2026.03.17",
        latest: "2026.07.09",
        age_days: 0,
        is_stale: true,
        auto_update: false,
      }),
      upgradeYtdlp: vi.fn().mockResolvedValue({
        ok: true,
        old: "2026.03.17",
        new: "2026.07.09",
        output: "",
        restart_scheduled: true,
      }),
    },
    bilibili: {
      status: vi.fn().mockResolvedValue({ logged_in: false, message: "未登录" }),
    },
    xiaohongshu: {
      status: vi.fn().mockResolvedValue({
        configured_cookie: false,
        storage_state_path: "",
        storage_state_exists: false,
        cookie_count: 0,
        login_cookie: false,
      }),
      login: vi.fn().mockResolvedValue({
        configured_cookie: false,
        storage_state_path: "",
        storage_state_exists: false,
        cookie_count: 0,
        login_cookie: false,
      }),
    },
    twitter: {
      status: vi.fn().mockResolvedValue({
        storage_state_path: "",
        storage_state_exists: false,
        cookie_count: 0,
        logged_in: false,
      }),
      login: vi.fn().mockResolvedValue({
        storage_state_path: "",
        storage_state_exists: true,
        cookie_count: 2,
        logged_in: true,
      }),
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
          {
            id: "xiaohongshu",
            preferred_quality: null,
            prefer_subtitle: false,
            image_strategy_order: ["raw_url", "cdn_fallback", "browser_request", "browser_interactive"],
            fail_on_missing_images: true,
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
  it("renders six hierarchical tabs", async () => {
    render(<SettingsPanel />)

    await screen.findByRole("button", { name: "Overall" })
    const nav = screen.getByRole("navigation")
    const labels = within(nav).getAllByRole("button").map((button) => button.textContent?.trim())

    expect(labels).toEqual([
      "Overall",
      "Knowledge Base",
      "Providers",
      "Services",
      "Local Models",
      "Pipelines/Sources",
    ])
  })

  it("places Jina Reader settings under Services", async () => {
    render(<SettingsPanel />)

    fireEvent.click(await screen.findByRole("button", { name: "Services" }))

    expect(await screen.findByText("yt-dlp")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "更新到最新并重启后端" })).toBeInTheDocument()
    expect(await screen.findByText("网页抓取")).toBeInTheDocument()
    expect(screen.getByText("启用 Defuddle")).toBeInTheDocument()
    expect(screen.getByText("启用 Playwright")).toBeInTheDocument()
    expect(screen.getByText("启用 Jina Reader")).toBeInTheDocument()
    expect(screen.getByDisplayValue("https://r.jina.ai")).toBeInTheDocument()
    expect(screen.getByDisplayValue("30")).toBeInTheDocument()
  })

  it("places platform source settings under Pipelines/Sources", async () => {
    render(<SettingsPanel />)

    fireEvent.click(await screen.findByRole("button", { name: "Pipelines/Sources" }))

    expect(await screen.findByText("哔哩哔哩")).toBeInTheDocument()
    expect(screen.getByText("登录凭据")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("必填：仅填写 SESSDATA 的值")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "检测登录" })).toBeInTheDocument()
    expect(screen.getByText("YouTube")).toBeInTheDocument()
    expect(screen.getByText("小宇宙")).toBeInTheDocument()
    expect(screen.getByText("小红书")).toBeInTheDocument()
    expect(screen.getByText("知乎")).toBeInTheDocument()
  })

  it("shows model purposes in Overall and server details in list-detail panels", async () => {
    render(<SettingsPanel />)

    expect(await screen.findByText("模型用途")).toBeInTheDocument()
    expect(screen.getByText("字幕简单润色")).toBeInTheDocument()
    expect(screen.getByText("ASR")).toBeInTheDocument()
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(8)
    expect(screen.queryByText(/当前：/)).not.toBeInTheDocument()
    expect(screen.getByDisplayValue("BAAI/bge-m3 · SiliconFlow")).toBeInTheDocument()

    fireEvent.click(await screen.findByRole("button", { name: "Providers" }))

    const providerSearch = await screen.findByPlaceholderText("搜索 Providers...")
    const providerList = providerSearch.closest("aside")
    expect(providerList).not.toBeNull()
    expect(within(providerList as HTMLElement).getByRole("button", { name: /DeepSeek/ })).toBeInTheDocument()
    const siliconFlowButton = within(providerList as HTMLElement).getByRole("button", { name: /SiliconFlow/ })
    expect(siliconFlowButton).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /本地 HF 模型/ })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Vision Server/ })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Purpose Binding/ })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /添加 Provider/ })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "批量导入" })).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "能力筛选" })).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Provider 筛选" })).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "启用状态筛选" })).not.toBeInTheDocument()

    fireEvent.click(siliconFlowButton)
    expect(screen.getByRole("heading", { name: "SiliconFlow" })).toBeInTheDocument()
    expect(screen.getByText("API Base")).toBeInTheDocument()
    expect(screen.getByText("API Key")).toBeInTheDocument()
    expect(screen.getByDisplayValue("https://api.siliconflow.cn/v1")).toBeInTheDocument()
    expect(screen.getAllByDisplayValue("BAAI/bge-m3").length).toBeGreaterThan(0)
    expect(screen.getAllByDisplayValue("Embedding").length).toBeGreaterThan(0)
    expect(screen.getAllByDisplayValue("Rerank").length).toBeGreaterThan(0)
    expect(screen.getAllByDisplayValue("VLM").length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole("button", { name: "获取模型" }))
    await waitFor(() => expect(api.settings.fetchProviderModels).toHaveBeenCalledWith("siliconflow"))
    expect(await screen.findByText("远端模型目录")).toBeInTheDocument()
    expect(screen.getByText("允许使用")).toBeInTheDocument()
    expect(screen.getByText("加入配置")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "同步模型" }))
    await waitFor(() => expect(api.settings.syncProviderModels).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole("button", { name: "添加模型" }))
    expect(screen.getByPlaceholderText("模型 ID，例如 Qwen/Qwen3.5-8B")).toBeInTheDocument()
    expect(screen.getByLabelText("模型类型")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Local Models" }))

    expect(await screen.findByPlaceholderText("搜索本地模型...")).toBeInTheDocument()
    expect(await screen.findByRole("heading", { name: "Qwen3-ASR GGUF" })).toBeInTheDocument()
    expect(screen.queryByText("默认 ASR")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /SiliconFlow ASR/ })).not.toBeInTheDocument()
    expect(screen.getByText("音频流程")).toBeInTheDocument()
    expect(screen.queryByText("Local LLM Server")).not.toBeInTheDocument()
    expect(screen.queryByText("Voiceprint Purpose")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("音频流程"))
    expect(screen.getByRole("heading", { name: "音频流程" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "ASR + pyannote" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "MOSS 一步转录" })).toBeInTheDocument()
    expect(screen.queryByText("启用声纹识别")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("UVR Server"))
    expect(screen.getByRole("heading", { name: "UVR Server" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "检查本机 UVR" })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText("可选：直接指定当前模型文件所在目录")).not.toBeInTheDocument()
  })
})
