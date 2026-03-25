import { SettingsPanel } from "@/components/settings-panel"

export function SettingsPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto p-6">
        <h1 className="text-lg font-semibold mb-6">设置</h1>
        <SettingsPanel />
      </div>
    </div>
  )
}
