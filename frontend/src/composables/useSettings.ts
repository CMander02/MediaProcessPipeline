import { ref, onMounted } from "vue"
import type { Settings } from "@/types"
import { settingsApi } from "@/api"

const defaultSettings: Settings = {
  // LLM 配置
  llm_provider: "anthropic",
  // Anthropic
  anthropic_api_key: "",
  anthropic_api_base: "",
  anthropic_model: "claude-sonnet-4-20250514",
  // OpenAI
  openai_api_key: "",
  openai_api_base: "",
  openai_model: "gpt-4o",
  // Custom (OpenAI Compatible)
  custom_api_key: "",
  custom_api_base: "",
  custom_model: "",
  custom_name: "Custom",

  // WhisperX
  whisper_model: "large-v3-turbo",
  whisper_model_path: "",
  whisper_device: "cuda",
  whisper_compute_type: "float16",
  whisper_batch_size: 16,
  enable_diarization: true,
  hf_token: "",
  pyannote_model_path: "",
  pyannote_segmentation_path: "",
  alignment_model_zh: "",
  alignment_model_en: "",
  diarization_batch_size: 16,

  // Paths
  inbox_path: "./data/inbox",
  processing_path: "./data/processing",
  outputs_path: "./data/outputs",
  archive_path: "./data/archive",
  obsidian_vault_path: "",

  // UVR
  uvr_model: "UVR-MDX-NET-Inst_HQ_3",
  uvr_device: "cuda",
  uvr_model_dir: "",
  uvr_mdx_inst_hq3_path: "",
  uvr_hp_uvr_path: "",
  uvr_denoise_lite_path: "",
  uvr_kim_vocal_2_path: "",
  uvr_deecho_dereverb_path: "",
  uvr_htdemucs_path: "",
}

export function useSettings() {
  const settings = ref<Settings>({ ...defaultSettings })
  const saving = ref(false)
  const saved = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  /**
   * Load settings from backend (primary source)
   */
  const loadSettings = async () => {
    loading.value = true
    error.value = null

    try {
      const backendSettings = await settingsApi.get()
      // Merge with defaults to ensure all fields exist
      settings.value = { ...defaultSettings, ...backendSettings } as Settings
      console.log("Settings loaded from backend")
    } catch (e) {
      console.warn("Failed to load settings from backend:", e)
      error.value = "Failed to load settings from server"
      // Keep default settings
    } finally {
      loading.value = false
    }
  }

  /**
   * Save settings to backend (primary storage)
   */
  const saveSettings = async () => {
    saving.value = true
    error.value = null

    try {
      await settingsApi.update(settings.value as unknown as Record<string, unknown>)
      console.log("Settings saved to backend")
      saved.value = true
      setTimeout(() => {
        saved.value = false
      }, 2000)
    } catch (e) {
      console.error("Failed to save settings:", e)
      error.value = "Failed to save settings to server"
    } finally {
      saving.value = false
    }
  }

  const updateSetting = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    settings.value = { ...settings.value, [key]: value }
  }

  onMounted(async () => {
    await loadSettings()
  })

  return {
    settings,
    saving,
    saved,
    loading,
    error,
    saveSettings,
    updateSetting,
    loadSettings,
  }
}
