import { useEffect, useState } from "react"

export interface LLMSettings {
  provider: "anthropic" | "openai" | "deepseek" | "custom"
  apiKey: string
  apiBase: string
  model: string
  temperature: number
}

const DEFAULT_SETTINGS: LLMSettings = {
  provider: "openai",
  apiKey: "",
  apiBase: "",
  model: "",
  temperature: 0.1,
}

const PROVIDER_DEFAULTS: Record<string, { model: string; apiBase: string }> = {
  anthropic: { model: "claude-sonnet-4-20250514", apiBase: "" },
  openai: { model: "gpt-4o", apiBase: "" },
  deepseek: { model: "deepseek-chat", apiBase: "https://api.deepseek.com/v1" },
  custom: { model: "", apiBase: "" },
}

export async function loadSettings(): Promise<LLMSettings> {
  const { llmSettings } = await chrome.storage.local.get("llmSettings")
  return llmSettings ? { ...DEFAULT_SETTINGS, ...llmSettings } : DEFAULT_SETTINGS
}

export async function saveSettings(settings: LLMSettings): Promise<void> {
  await chrome.storage.local.set({ llmSettings: settings })
}

export function SettingsView({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState<LLMSettings>(DEFAULT_SETTINGS)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    loadSettings().then(setSettings)
  }, [])

  const handleProviderChange = (provider: LLMSettings["provider"]) => {
    const defaults = PROVIDER_DEFAULTS[provider]
    setSettings((s) => ({
      ...s,
      provider,
      model: defaults.model,
      apiBase: defaults.apiBase,
    }))
  }

  const handleSave = async () => {
    await saveSettings(settings)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <button onClick={onBack} className="text-sm text-blue-600 hover:text-blue-800">
          ← Back
        </button>
        <h2 className="text-sm font-medium">LLM Settings</h2>
      </div>
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-gray-500">Provider</label>
          <select
            value={settings.provider}
            onChange={(e) => handleProviderChange(e.target.value as LLMSettings["provider"])}
            className="w-full rounded border px-2 py-1.5 text-sm"
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="deepseek">DeepSeek</option>
            <option value="custom">Custom (OpenAI-compatible)</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-gray-500">API Key</label>
          <input
            type="password"
            value={settings.apiKey}
            onChange={(e) => setSettings((s) => ({ ...s, apiKey: e.target.value }))}
            placeholder="sk-..."
            className="w-full rounded border px-2 py-1.5 text-sm"
          />
        </div>
        {(settings.provider === "custom" || settings.provider === "deepseek") && (
          <div>
            <label className="mb-1 block text-xs text-gray-500">API Base URL</label>
            <input
              type="url"
              value={settings.apiBase}
              onChange={(e) => setSettings((s) => ({ ...s, apiBase: e.target.value }))}
              placeholder="https://api.example.com/v1"
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </div>
        )}
        <div>
          <label className="mb-1 block text-xs text-gray-500">Model</label>
          <input
            value={settings.model}
            onChange={(e) => setSettings((s) => ({ ...s, model: e.target.value }))}
            placeholder="gpt-4o"
            className="w-full rounded border px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-gray-500">
            Temperature ({settings.temperature})
          </label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            value={settings.temperature}
            onChange={(e) => setSettings((s) => ({ ...s, temperature: parseFloat(e.target.value) }))}
            className="w-full"
          />
        </div>
        <button
          onClick={handleSave}
          className="w-full rounded-md bg-blue-600 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          {saved ? "Saved ✓" : "Save Settings"}
        </button>
      </div>
    </div>
  )
}
