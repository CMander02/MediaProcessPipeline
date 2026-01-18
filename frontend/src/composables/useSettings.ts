import { ref, onMounted } from "vue"
import type { Settings } from "@/types"

const STORAGE_KEY = "pipeline-settings"

const defaultSettings: Settings = {
  anthropic_api_key: "",
  openai_api_key: "",
  llm_provider: "anthropic",
  llm_model: "claude-sonnet-4-20250514",
  whisper_model: "large-v3",
  whisper_device: "cuda",
  whisper_compute_type: "float16",
  enable_diarization: true,
  hf_token: "",
  inbox_path: "./data/inbox",
  processing_path: "./data/processing",
  outputs_path: "./data/outputs",
  archive_path: "./data/archive",
  obsidian_vault_path: "",
  uvr_model: "Kim_Vocal_2",
  uvr_device: "cuda",
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
