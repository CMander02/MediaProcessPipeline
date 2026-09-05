import { type Settings } from "@/lib/api"
import { useRef } from "react"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { Delete01Icon, PlusSignIcon } from "@hugeicons/core-free-icons"
import { SettingRow } from "../setting-controls"
import { type UpdateSettings, type CustomProfile } from "./types"
import { getCustomProfiles } from "./registry-utils"

export function CustomProfilesEditor({
  settings,
  updateSettings,
}: {
  settings: Settings
  updateSettings: UpdateSettings
}) {
  const profiles = getCustomProfiles(settings)
  const activeId = String(settings.custom_active_profile_id ?? profiles[0]?.id ?? "default")
  const activeProfile = profiles.find((profile) => profile.id === activeId) ?? profiles[0]
  const nextProfileIdRef = useRef(1)

  const saveProfiles = async (next: CustomProfile[], nextActive = activeId) => {
    const active = next.find((profile) => profile.id === nextActive) ?? next[0]
    await updateSettings({
      custom_llm_profiles: next,
      custom_active_profile_id: active.id,
      custom_name: active.name,
      custom_api_base: active.api_base,
      custom_model: active.model,
      custom_api_key: active.api_key,
    })
  }

  const updateProfile = (field: keyof CustomProfile, value: string) => {
    const next = profiles.map((profile) =>
      profile.id === activeProfile.id ? { ...profile, [field]: value } : profile,
    )
    void saveProfiles(next, activeProfile.id)
  }

  const addProfile = () => {
    const profileId = `custom-${nextProfileIdRef.current}`
    nextProfileIdRef.current += 1
    const nextProfile: CustomProfile = {
      id: profileId,
      name: `Custom ${profiles.length + 1}`,
      api_base: "",
      model: "",
      api_key: "",
    }
    void saveProfiles([...profiles, nextProfile], nextProfile.id)
  }

  const removeProfile = () => {
    if (profiles.length <= 1) return
    const next = profiles.filter((profile) => profile.id !== activeProfile.id)
    void saveProfiles(next, next[0].id)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Label className="w-24 shrink-0 text-sm text-muted-foreground">配置</Label>
        <select
          value={activeProfile.id}
          onChange={(event) => void saveProfiles(profiles, event.target.value)}
          className="h-8 min-w-52 rounded-md border border-input bg-background px-3 text-sm"
        >
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
          ))}
        </select>
        <Button size="sm" variant="ghost" onClick={addProfile} className="h-8 gap-1.5 px-2">
          <HugeiconsIcon icon={PlusSignIcon} className="h-3.5 w-3.5" />
          新增
        </Button>
        <Button size="sm" variant="ghost" onClick={removeProfile} disabled={profiles.length <= 1} className="h-8 gap-1.5 px-2 text-destructive hover:text-destructive">
          <HugeiconsIcon icon={Delete01Icon} className="h-3.5 w-3.5" />
          删除
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <SettingRow label="名称" settingKey="custom_profile_name" value={activeProfile.name} onSave={async (_key, value) => updateProfile("name", String(value))} saving={{}} saved={{}} />
        <SettingRow label="模型" settingKey="custom_profile_model" value={activeProfile.model} onSave={async (_key, value) => updateProfile("model", String(value))} saving={{}} saved={{}} />
        <SettingRow label="API Base" settingKey="custom_profile_base" value={activeProfile.api_base} onSave={async (_key, value) => updateProfile("api_base", String(value))} saving={{}} saved={{}} />
        <SettingRow label="API Key" settingKey="custom_profile_key" value={activeProfile.api_key} onSave={async (_key, value) => updateProfile("api_key", String(value))} saving={{}} saved={{}} masked />
      </div>
    </div>
  )
}
