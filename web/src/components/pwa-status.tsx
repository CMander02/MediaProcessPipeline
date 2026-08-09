import { useEffect, useRef, useState } from "react"
import { registerSW } from "virtual:pwa-register"

import { Button } from "@/components/ui/button"
import { usePlatform } from "@/platform/use-platform"

export function PwaStatus() {
  const platform = usePlatform()
  const [needRefresh, setNeedRefresh] = useState(false)
  const [offlineReady, setOfflineReady] = useState(false)
  const updateServiceWorker = useRef<((reloadPage?: boolean) => Promise<void>) | null>(null)

  useEffect(() => {
    if (platform.isNative || !import.meta.env.PROD || !("serviceWorker" in navigator)) return
    updateServiceWorker.current = registerSW({
      immediate: true,
      onNeedRefresh: () => setNeedRefresh(true),
      onOfflineReady: () => {
        setOfflineReady(true)
        window.setTimeout(() => setOfflineReady(false), 4000)
      },
    })
  }, [platform.isNative])

  if (platform.isNative) return null

  if (!needRefresh && !offlineReady) return null

  return (
    <div className="fixed inset-x-3 bottom-[calc(4.75rem+var(--mpp-safe-bottom))] z-[100] mx-auto flex max-w-md items-center justify-between gap-3 rounded-lg border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-lg md:bottom-4">
      <span>{needRefresh ? "MPP 新版本已经准备好。" : "MPP 已可离线启动。"}</span>
      {needRefresh ? (
        <Button className="h-11 shrink-0 md:h-8" size="sm" onClick={() => void updateServiceWorker.current?.(true)}>
          刷新
        </Button>
      ) : (
        <Button className="h-11 shrink-0 md:h-8" size="sm" variant="ghost" onClick={() => setOfflineReady(false)}>
          知道了
        </Button>
      )}
    </div>
  )
}
