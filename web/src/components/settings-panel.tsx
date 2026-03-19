import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { api, type Settings } from "@/lib/api"
import { Save, Check } from "lucide-react"

export function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [editKey, setEditKey] = useState("")
  const [editValue, setEditValue] = useState("")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.settings.get().then(setSettings).catch(() => {})
  }, [])

  const switchAsr = async (backend: string) => {
    const updated = await api.settings.patch({ asr_backend: backend })
    setSettings(updated)
  }

  const saveSetting = async () => {
    if (!editKey) return
    setSaving(true)
    try {
      let value: unknown = editValue
      if (editValue === "true") value = true
      else if (editValue === "false") value = false
      else if (!isNaN(Number(editValue)) && editValue !== "") value = Number(editValue)

      const updated = await api.settings.patch({ [editKey]: value })
      setSettings(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {}
    setSaving(false)
  }

  if (!settings) return <p className="text-sm text-muted-foreground">Loading&hellip;</p>

  const commonKeys = [
    { group: "ASR", keys: ["asr_backend", "qwen3_device", "qwen3_asr_model_path", "whisper_model"] },
    { group: "LLM", keys: ["llm_provider", "custom_name", "custom_model", "custom_api_base"] },
    { group: "处理", keys: ["uvr_model", "uvr_device", "enable_diarization"] },
    { group: "路径", keys: ["data_root", "obsidian_vault_path"] },
  ]

  return (
    <div className="space-y-6">
      {/* ASR quick toggle */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">ASR 后端</CardTitle>
        </CardHeader>
        <CardContent>
          <RadioGroup
            value={settings.asr_backend}
            onValueChange={switchAsr}
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
        </CardContent>
      </Card>

      {/* Settings table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">常用设置</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {commonKeys.map(({ group, keys }) => (
              <div key={group}>
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">{group}</h3>
                <div className="grid gap-1.5">
                  {keys.map((k) => {
                    const v = settings[k]
                    const display = typeof v === "string" && k.includes("api_key") && v
                      ? `${v.slice(0, 8)}\u2026`
                      : String(v ?? "")
                    return (
                      <div key={k} className="flex items-center gap-3 py-1 text-sm">
                        <code className="text-xs text-muted-foreground w-48 shrink-0 truncate">{k}</code>
                        <span className="truncate flex-1">{display || <span className="text-muted-foreground italic">empty</span>}</span>
                      </div>
                    )
                  })}
                </div>
                <Separator className="mt-3" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Edit setting */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">修改设置</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Input
              placeholder="key"
              value={editKey}
              onChange={(e) => setEditKey(e.target.value)}
              className="w-48"
              autoComplete="off"
            />
            <Input
              placeholder="value"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              className="flex-1"
              autoComplete="off"
            />
            <Button onClick={saveSetting} disabled={!editKey || saving}>
              {saved ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
              <span className="ml-1.5">{saved ? "已保存" : "保存"}</span>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
