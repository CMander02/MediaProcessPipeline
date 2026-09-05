import { SettingRow } from "../setting-controls"
import { type SharedSettingsProps } from "./types"

type DeepSeekConfigProps = SharedSettingsProps

export function DeepSeekConfig({ settings, updateSetting, saving, saved }: DeepSeekConfigProps) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        DeepSeek v4 原生 API，按阶段独立配置 model / thinking / reasoning_effort。
        常用模型名包括 deepseek-v4-flash 和 deepseek-v4-pro。
      </p>
      <SettingRow
        label="API Base"
        settingKey="deepseek_api_base"
        value={String(settings.deepseek_api_base ?? "https://api.deepseek.com")}
        onSave={updateSetting}
        saving={saving}
        saved={saved}
      />
      <SettingRow
        label="API Key"
        settingKey="deepseek_api_key"
        value={String(settings.deepseek_api_key ?? "")}
        onSave={updateSetting}
        saving={saving}
        saved={saved}
        masked
      />
    </div>
  )
}
