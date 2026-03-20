import { useEffect, useState, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { api, type Settings } from "@/lib/api"
import { usePreferences } from "@/hooks/use-preferences"
import { Save, Check, Moon, Sun } from "lucide-react"

export function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [darkMode, setDarkMode] = useState(
    () => document.documentElement.classList.contains("dark"),
  )
  const { prefs, update: updatePrefs } = usePreferences()

  useEffect(() => {
    api.settings.get().then(setSettings).catch(() => {})
  }, [])

  const updateSetting = useCallback(
    async (key: string, value: unknown) => {
      setSaving((s) => ({ ...s, [key]: true }))
      try {
        const updated = await api.settings.patch({ [key]: value })
        setSettings(updated)
        setSaved((s) => ({ ...s, [key]: true }))
        setTimeout(() => setSaved((s) => ({ ...s, [key]: false })), 1500)
      } catch {}
      setSaving((s) => ({ ...s, [key]: false }))
    },
    [],
  )

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    document.documentElement.classList.toggle("dark", next)
    localStorage.setItem("theme", next ? "dark" : "light")
  }

  if (!settings) {
    return <p className="text-sm text-muted-foreground">加载中...</p>
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Appearance */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">外观</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {darkMode ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
              <Label>深色模式</Label>
            </div>
            <Switch checked={darkMode} onCheckedChange={toggleDark} />
          </div>
        </CardContent>
      </Card>

      {/* Startup page */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">启动页面</CardTitle>
        </CardHeader>
        <CardContent>
          <RadioGroup
            value={prefs.startupPage}
            onValueChange={(v) => updatePrefs({ startupPage: v as "files" | "last" })}
            className="flex gap-4"
          >
            <div className="flex items-center gap-2">
              <RadioGroupItem value="files" id="startup-files" />
              <Label htmlFor="startup-files">文件列表</Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="last" id="startup-last" />
              <Label htmlFor="startup-last">上次打开的归档</Label>
            </div>
          </RadioGroup>
        </CardContent>
      </Card>

      {/* ASR Backend */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">ASR 后端</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <RadioGroup
            value={settings.asr_backend}
            onValueChange={(v) => updateSetting("asr_backend", v)}
            className="flex gap-4"
          >
            <div className="flex items-center gap-2">
              <RadioGroupItem value="qwen3" id="asr-qwen3" />
              <Label htmlFor="asr-qwen3">Qwen3-ASR</Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="whisperx" id="asr-whisperx" />
              <Label htmlFor="asr-whisperx">WhisperX</Label>
            </div>
          </RadioGroup>

          <Separator />

          {settings.asr_backend === "qwen3" ? (
            <div className="space-y-3">
              <SettingRow
                label="模型路径"
                settingKey="qwen3_asr_model_path"
                value={String(settings.qwen3_asr_model_path ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="设备"
                settingKey="qwen3_device"
                value={String(settings.qwen3_device ?? "cuda")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
            </div>
          ) : (
            <div className="space-y-3">
              <SettingRow
                label="Whisper 模型"
                settingKey="whisper_model"
                value={String(settings.whisper_model ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="模型路径"
                settingKey="whisper_model_path"
                value={String(settings.whisper_model_path ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* LLM Provider */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">LLM 提供商</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <RadioGroup
            value={settings.llm_provider}
            onValueChange={(v) => updateSetting("llm_provider", v)}
            className="flex gap-4"
          >
            <div className="flex items-center gap-2">
              <RadioGroupItem value="anthropic" id="llm-anthropic" />
              <Label htmlFor="llm-anthropic">Anthropic</Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="openai" id="llm-openai" />
              <Label htmlFor="llm-openai">OpenAI</Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="custom" id="llm-custom" />
              <Label htmlFor="llm-custom">Custom</Label>
            </div>
          </RadioGroup>

          <Separator />

          {settings.llm_provider === "custom" ? (
            <div className="space-y-3">
              <SettingRow
                label="名称"
                settingKey="custom_name"
                value={String(settings.custom_name ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="API Base"
                settingKey="custom_api_base"
                value={String(settings.custom_api_base ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="模型"
                settingKey="custom_model"
                value={String(settings.custom_model ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="API Key"
                settingKey="custom_api_key"
                value={String(settings.custom_api_key ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                masked
              />
            </div>
          ) : settings.llm_provider === "anthropic" ? (
            <div className="space-y-3">
              <SettingRow
                label="模型"
                settingKey="anthropic_model"
                value={String(settings.anthropic_model ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="API Key"
                settingKey="anthropic_api_key"
                value={String(settings.anthropic_api_key ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                masked
              />
            </div>
          ) : (
            <div className="space-y-3">
              <SettingRow
                label="模型"
                settingKey="openai_model"
                value={String(settings.openai_model ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="API Key"
                settingKey="openai_api_key"
                value={String(settings.openai_api_key ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                masked
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Data Paths */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">数据路径</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow
            label="数据根目录"
            settingKey="data_root"
            value={String(settings.data_root ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
          <SettingRow
            label="Obsidian 库路径"
            settingKey="obsidian_vault_path"
            value={String(settings.obsidian_vault_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
        </CardContent>
      </Card>

      {/* UVR */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">UVR 人声分离</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow
            label="模型"
            settingKey="uvr_model"
            value={String(settings.uvr_model ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
          <SettingRow
            label="设备"
            settingKey="uvr_device"
            value={String(settings.uvr_device ?? "cuda")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
        </CardContent>
      </Card>
    </div>
  )
}

// --- Reusable setting row ---

interface SettingRowProps {
  label: string
  settingKey: string
  value: string
  onSave: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
  masked?: boolean
}

function SettingRow({ label, settingKey, value, onSave, saving, saved, masked }: SettingRowProps) {
  const [editValue, setEditValue] = useState(value)

  // Sync with external value
  useEffect(() => {
    setEditValue(value)
  }, [value])

  const isDirty = editValue !== value
  const isSaving = saving[settingKey]
  const isSaved = saved[settingKey]

  const handleSave = () => {
    if (!isDirty) return
    onSave(settingKey, editValue)
  }

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        type={masked ? "password" : "text"}
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
        className="flex-1 h-8 text-sm"
        autoComplete="off"
      />
      {isDirty && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleSave}
          disabled={isSaving}
          className="h-8 px-2"
        >
          {isSaved ? <Check className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
        </Button>
      )}
      {!isDirty && isSaved && (
        <Check className="h-3.5 w-3.5 text-emerald-500" />
      )}
    </div>
  )
}
