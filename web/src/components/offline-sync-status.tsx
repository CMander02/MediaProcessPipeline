import { useState } from "react"
import { Download01Icon, Loading03Icon, RefreshIcon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { useOfflineSync } from "@/hooks/use-offline-sync"
import { cn } from "@/lib/utils"
import { usePlatform } from "@/platform/use-platform"

export function OfflineSyncStatus({ compact = false }: { compact?: boolean }) {
  const platform = usePlatform()
  const { online, authExpired } = useAppAccess()
  const { status, refresh, syncNow, clear, rebuild } = useOfflineSync()
  const [busy, setBusy] = useState<"sync" | "clear" | "rebuild" | null>(null)
  const progress = status.totalBytes > 0
    ? Math.round((status.completedBytes / status.totalBytes) * 100)
    : status.totalFiles > 0
      ? Math.round((status.completedFiles / status.totalFiles) * 100)
      : 0

  if (!platform.isNative) return null

  const run = async (action: "sync" | "clear" | "rebuild") => {
    if (action === "clear" && !window.confirm("清空当前手机中的全部离线资料？服务器资料保持不变。")) return
    if (action === "rebuild" && !window.confirm("重新建立当前手机的离线索引？已有文件会在校验后继续复用。")) return
    setBusy(action)
    try {
      if (action === "sync") await syncNow()
      if (action === "clear") await clear()
      if (action === "rebuild") await rebuild()
    } catch {
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  const label = authExpired
    ? "访问令牌已失效"
    : status.syncing
      ? status.message || `正在同步 ${status.completedFiles}/${status.totalFiles}`
      : status.lastError
        ? "同步等待重试"
        : online
          ? `${status.archiveCount} 份资料已离线可用`
          : `${status.archiveCount} 份离线资料`

  if (compact) {
    return (
      <div className="flex min-h-9 items-center gap-2 rounded-lg border bg-card px-3 py-1.5 text-xs text-muted-foreground">
        <HugeiconsIcon
          icon={status.syncing ? Loading03Icon : Download01Icon}
          className={cn("size-4 shrink-0", status.syncing && "animate-spin")}
        />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {status.syncing && <Progress value={progress} className="h-1.5 w-20" />}
        <Button
          className="h-7 px-2.5 text-xs"
          variant="ghost"
          disabled={!online || status.syncing || busy !== null || authExpired}
          onClick={() => void run("sync")}
        >
          同步
        </Button>
      </div>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={Download01Icon} className="size-4" />
          离线资料
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <p className="text-sm font-medium">{label}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {status.lastSync > 0 ? `上次同步 ${new Date(status.lastSync).toLocaleString()}` : "连接服务器后自动建立离线资料库。"}
          </p>
          {status.lastError && <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{status.lastError}</p>}
        </div>
        {status.syncing && (
          <div className="space-y-1.5">
            <Progress value={progress} />
            <p className="text-xs text-muted-foreground">
              {status.completedFiles}/{status.totalFiles} 个文件 · {formatBytes(status.completedBytes)}/{formatBytes(status.totalBytes)}
            </p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button disabled={!online || status.syncing || busy !== null || authExpired} onClick={() => void run("sync")}>
            <HugeiconsIcon icon={busy === "sync" || status.syncing ? Loading03Icon : RefreshIcon} className={cn("mr-2 size-4", (busy === "sync" || status.syncing) && "animate-spin")} />
            立即同步
          </Button>
          <Button variant="outline" disabled={!online || status.syncing || busy !== null || authExpired} onClick={() => void run("rebuild")}>
            重建索引
          </Button>
          <Button variant="outline" disabled={status.syncing || busy !== null || status.archiveCount === 0} onClick={() => void run("clear")}>
            清空离线资料
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const amount = value / (1024 ** exponent)
  return `${amount >= 10 || exponent === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[exponent]}`
}
