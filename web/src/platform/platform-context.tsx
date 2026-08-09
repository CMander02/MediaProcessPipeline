import { useEffect, type ReactNode } from "react"

import type { PlatformAdapter } from "@/platform/types"
import { PlatformContext } from "@/platform/platform-context-state"
import { watchSystemTheme } from "@/lib/theme"

export function PlatformProvider({ adapter, children }: { adapter: PlatformAdapter; children: ReactNode }) {
  useEffect(() => watchSystemTheme(), [])

  useEffect(() => {
    const sync = () => void adapter.syncTheme(document.documentElement.classList.contains("dark"))
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    sync()
    return () => observer.disconnect()
  }, [adapter])

  useEffect(() => {
    if (!adapter.isNative) return
    const handleAnchor = (event: MouseEvent) => {
      const target = event.target as Element | null
      const anchor = target?.closest("a")
      if (!(anchor instanceof HTMLAnchorElement) || !anchor.href) return
      if (anchor.href.startsWith(`${window.location.origin}/#`) || anchor.href.startsWith("blob:")) return
      if (!/^https?:/i.test(anchor.href)) return
      event.preventDefault()
      if (anchor.hasAttribute("download")) void adapter.download(anchor.href)
      else void adapter.openExternal(anchor.href)
    }
    document.addEventListener("click", handleAnchor)
    return () => document.removeEventListener("click", handleAnchor)
  }, [adapter])

  return <PlatformContext.Provider value={adapter}>{children}</PlatformContext.Provider>
}
