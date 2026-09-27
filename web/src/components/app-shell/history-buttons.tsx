import { useEffect, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft02Icon, ArrowRight02Icon } from "@hugeicons/core-free-icons"

import { cn } from "@/lib/utils"

// The Navigation API (Chromium, so the desktop app) knows whether back/forward are possible.
interface NavigationLike extends EventTarget {
  canGoBack: boolean
  canGoForward: boolean
  back(): { finished?: Promise<unknown> }
  forward(): { finished?: Promise<unknown> }
}

function navigationApi(): NavigationLike | null {
  return (window as unknown as { navigation?: NavigationLike }).navigation ?? null
}

function go(direction: "back" | "forward") {
  const nav = navigationApi()
  if (!nav) {
    if (direction === "back") window.history.back()
    else window.history.forward()
    return
  }
  if (direction === "back" ? !nav.canGoBack : !nav.canGoForward) return
  // A navigation interrupted by another one rejects `finished`; that is expected.
  nav[direction]().finished?.catch(() => {})
}

/** Back / forward for the desktop app, which has no browser toolbar. Also Alt+←/→ and mouse side buttons. */
export function HistoryButtons({ className }: { className?: string }) {
  const [state, setState] = useState(() => {
    const nav = navigationApi()
    return { back: nav?.canGoBack ?? true, forward: nav?.canGoForward ?? true }
  })

  useEffect(() => {
    const nav = navigationApi()
    const update = () => {
      if (nav) setState({ back: nav.canGoBack, forward: nav.canGoForward })
    }
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      if (event.key === "ArrowLeft") { event.preventDefault(); go("back") }
      if (event.key === "ArrowRight") { event.preventDefault(); go("forward") }
    }
    const onMouse = (event: MouseEvent) => {
      if (event.button === 3) { event.preventDefault(); go("back") }
      if (event.button === 4) { event.preventDefault(); go("forward") }
    }
    nav?.addEventListener("currententrychange", update)
    window.addEventListener("keydown", onKey)
    window.addEventListener("mouseup", onMouse)
    return () => {
      nav?.removeEventListener("currententrychange", update)
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("mouseup", onMouse)
    }
  }, [])

  const button = "flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
  return (
    <div className={cn("flex shrink-0 items-center", className)}>
      <button type="button" className={button} disabled={!state.back} onClick={() => go("back")} aria-label="后退" title="后退 (Alt+←)">
        <HugeiconsIcon icon={ArrowLeft02Icon} className="size-4" />
      </button>
      <button type="button" className={button} disabled={!state.forward} onClick={() => go("forward")} aria-label="前进" title="前进 (Alt+→)">
        <HugeiconsIcon icon={ArrowRight02Icon} className="size-4" />
      </button>
    </div>
  )
}
