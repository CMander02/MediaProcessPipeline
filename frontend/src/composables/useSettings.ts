import { ref, onMounted } from "vue"
import type { Settings } from "@/types"

const STORAGE_KEY = "pipeline-settings"

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
  enable_diarization: true,
  hf_token: "",
  pyannote_model_path: "",
  pyannote_segmentation_path: "",
  alignment_model_zh: "",
  alignment_model_en: "",

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

  const loadSettings = () => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      try {
        settings.value = { ...defaultSettings, ...JSON.parse(stored) }
      } catch {
        // Ignore parse errors
      }
    }
  }

  const saveSettings = async () => {
    saving.value = true
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings.value))
    await new Promise((r) => setTimeout(r, 500))
    saving.value = false
    saved.value = true
    setTimeout(() => {
      saved.value = false
    }, 2000)
  }

  const updateSetting = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    settings.value = { ...settings.value, [key]: value }
  }

  onMounted(() => {
    loadSettings()
  })

  return {
    settings,
    saving,
    saved,
    saveSettings,
    updateSetting,
    loadSettings,
  }
}
