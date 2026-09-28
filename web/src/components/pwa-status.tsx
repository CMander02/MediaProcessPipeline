import { useEffect, useState } from "react"
import { toast } from "sonner"
import { registerSW } from "virtual:pwa-register"

import { Button } from "@/components/ui/button"
import {
  consumeUpdatedFlag,
  reloadAfterMissingChunk,
  removeServiceWorkers,
  scheduleUpdateReload,
  serverHasNewBuild,
  watchForNewBuild,
} from "@/lib/app-updates"
import { isDesktopApp } from "@/lib/desktop-bridge"
import { usePlatform } from "@/platform/use-platform"

/** New builds load by themselves; in a browser the page also works offline once cached. */
export function PwaStatus() {
  const platform = usePlatform()
  const [offlineReady, setOfflineReady] = useState(false)

  useEffect(() => {
    if (platform.isNative || !import.meta.env.PROD) return
    if (consumeUpdatedFlag()) toast.success("MPP 已更新到新版本", { duration: 2500 })

    const onPreloadError = (event: Event) => {
      if (reloadAfterMissingChunk()) event.preventDefault()
    }
    window.addEventListener("vite:preloadError", onPreloadError)

    let stopWatching = () => {}
    if (isDesktopApp || !("serviceWorker" in navigator)) {
      void removeServiceWorkers()
      stopWatching = watchForNewBuild(serverHasNewBuild)
    } else {
      registerSW({
        immediate: true,
        // The new service worker takes over by itself; the page follows at a quiet moment.
        onNeedReload: scheduleUpdateReload,
        onOfflineReady: () => {
          setOfflineReady(true)
          window.setTimeout(() => setOfflineReady(false), 4000)
        },
        onRegisteredSW: (_url, registration) => {
          if (!registration) return
          stopWatching = watchForNewBuild(async () => {
            await registration.update()
            return false
          })
        },
      })
    }
    return () => {
      window.removeEventListener("vite:preloadError", onPreloadError)
      stopWatching()
    }
  }, [platform.isNative])

  if (platform.isNative || !offlineReady) return null

  return (
    <div className="fixed inset-x-3 bottom-[calc(4.75rem+var(--mpp-safe-bottom))] z-[100] mx-auto flex max-w-md items-center justify-between gap-3 rounded-lg border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-lg md:bottom-4">
      <span>MPP 已可离线启动。</span>
      <Button className="h-11 shrink-0 md:h-8" size="sm" variant="ghost" onClick={() => setOfflineReady(false)}>
        知道了
      </Button>
    </div>
  )
}
