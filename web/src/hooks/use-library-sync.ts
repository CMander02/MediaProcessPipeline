import { useEffect, useMemo } from "react"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { refreshLibraryDirectory } from "@/hooks/use-library-directory"
import { subscribeAllEvents } from "@/lib/api"
import { usePlatform } from "@/platform/use-platform"
import { createArchiveRepository } from "@/repositories/archive-repository"

/** One refresh loop for every view of the connected server's library. */
export function useLibrarySync() {
  const { online } = useAppAccess()
  const platform = usePlatform()
  const repository = useMemo(() => createArchiveRepository(platform), [platform])

  useEffect(() => {
    if (!online) return
    let disposed = false
    let running = false
    let again = false
    let timer = 0
    const refresh = async () => {
      if (disposed || document.visibilityState === "hidden") return
      if (running) { again = true; return }
      running = true
      try {
        await refreshLibraryDirectory()
      } catch {
        // The next event, foreground transition or periodic check retries.
      } finally {
        running = false
        if (!disposed) {
          window.dispatchEvent(new Event("mpp:library-refresh"))
          if (platform.isNative) repository.triggerSync()
          if (again) { again = false; schedule() }
        }
      }
    }
    const schedule = () => {
      if (timer) return
      timer = window.setTimeout(() => { timer = 0; void refresh() }, 300)
    }
    const unsubscribe = subscribeAllEvents((event) => {
      // Logs and per-frame progress must not cause a network request each time.
      if (!["log", "progress", "diagnostic", "heartbeat"].includes(event.type)) schedule()
    })
    const events = ["focus", "online", "mpp:app-resume", "mpp:connection-change", "mpp:workspace-change", "mpp:events-connected"]
    for (const event of events) window.addEventListener(event, schedule)
    document.addEventListener("visibilitychange", schedule)
    const interval = window.setInterval(() => void refresh(), 15_000)
    schedule()
    return () => {
      disposed = true
      window.clearTimeout(timer)
      window.clearInterval(interval)
      unsubscribe()
      for (const event of events) window.removeEventListener(event, schedule)
      document.removeEventListener("visibilitychange", schedule)
    }
  }, [online, platform.isNative, repository])
}
