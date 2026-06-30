import { SettingsPanel } from "@/components/settings-panel"
import { HugeiconsIcon } from "@hugeicons/react"
import { Settings01Icon } from "@hugeicons/core-free-icons"

export function SettingsPage() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="shrink-0 border-b bg-card">
        <div className="mx-auto flex w-full max-w-[1680px] items-center gap-3 px-6 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <HugeiconsIcon icon={Settings01Icon} className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold leading-tight">设置</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              配置处理管线、模型服务、平台账号和本地运行偏好。
            </p>
          </div>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <div className="mx-auto h-full min-h-0 w-full max-w-[1680px] px-6 py-5">
          <SettingsPanel />
        </div>
      </div>
    </div>
  )
}
