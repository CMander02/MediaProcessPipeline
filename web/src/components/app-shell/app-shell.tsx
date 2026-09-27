import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react"
import { Settings01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { ActivityPanel } from "@/components/activity/activity-panel"
import { DesktopHeader } from "@/components/app-shell/desktop-header"
import { HeaderSlotContext } from "@/components/app-shell/header-slot-context"
import { HistoryButtons } from "@/components/app-shell/history-buttons"
import { MobileBottomNav } from "@/components/app-shell/mobile-bottom-nav"
import { PAGE_TITLES } from "@/components/app-shell/navigation"
import { cssColorToHex, desktopApp } from "@/lib/desktop-bridge"
import { navigate, type Route } from "@/lib/router"
import { cn } from "@/lib/utils"

interface AppShellProps {
  activePage: Route["page"]
  toolbar?: ReactNode
  children: ReactNode
  runtimeControls?: boolean
}

// In the desktop app the title bar is ours; the window buttons are drawn over its right end
// (Window Controls Overlay), so that end is kept free.
const DESKTOP_TITLE_BAR: CSSProperties = {
  paddingLeft: "max(0.5rem, env(titlebar-area-x, 0px))",
  paddingRight: "calc(100vw - env(titlebar-area-x, 0px) - env(titlebar-area-width, 100vw) + 0.5rem)",
}

/** Keep the overlaid window buttons in the title bar's colours, including after a theme switch. */
function useTitleBarColors(header: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!desktopApp || !header.current) return
    const element = header.current
    let last = ""
    const sync = () => {
      const style = getComputedStyle(element)
      const color = cssColorToHex(style.backgroundColor)
      const symbolColor = cssColorToHex(style.color)
      if (!color || !symbolColor || `${color}${symbolColor}` === last) return
      last = `${color}${symbolColor}`
      desktopApp?.setTitleBarColors({ color, symbolColor }).catch(() => {})
    }
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] })
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    media.addEventListener("change", sync)
    return () => {
      observer.disconnect()
      media.removeEventListener("change", sync)
    }
  }, [header])
}

export function AppShell({ activePage, toolbar, children, runtimeControls = true }: AppShellProps) {
  const settingsActive = activePage === "settings"
  const [slot, setSlot] = useState<HTMLDivElement | null>(null)
  const header = useRef<HTMLElement>(null)
  useTitleBarColors(header)

  return (
    <HeaderSlotContext.Provider value={slot}>
      <div className="flex h-screen supports-[height:100dvh]:h-dvh flex-col overflow-hidden bg-background">
        <header ref={header} className="shrink-0 border-b bg-card text-foreground pt-[var(--mpp-safe-top)]">
          {/* One row on desktop (the window title bar in the desktop app); two rows on phones. */}
          <div
            className={cn(
              "group/header flex min-h-14 flex-wrap items-center gap-2 px-3 py-2",
              "md:h-11 md:min-h-0 md:flex-nowrap md:gap-1.5 md:py-0 md:pl-2 md:pr-2",
              desktopApp && "app-drag",
            )}
            style={desktopApp ? DESKTOP_TITLE_BAR : undefined}
          >
            {desktopApp && <HistoryButtons className="hidden md:flex" />}
            <DesktopHeader activePage={activePage} />

            <div className="flex min-w-0 flex-1 items-center gap-2 md:hidden" data-mobile-header>
              <img src="/favicon.svg" className="size-5 shrink-0" alt="" aria-hidden="true" />
              <h1 className="truncate text-base font-semibold tracking-tight">{PAGE_TITLES[activePage]}</h1>
            </div>

            {/* A page can fill this part of the title bar (see HeaderContext). */}
            <div ref={setSlot} data-header-slot className="hidden min-w-0 flex-1 items-center gap-2 md:flex md:empty:hidden" />

            {toolbar ? (
              <div className="order-last w-full min-w-0 pt-1 md:order-none md:w-auto md:flex-1 md:pt-0">
                {toolbar}
              </div>
            ) : (
              <div className="hidden min-w-0 flex-1 md:block md:group-has-[[data-header-slot]>*]/header:hidden" />
            )}

            <div className="ml-auto flex shrink-0 items-center gap-1">
              {runtimeControls && <ActivityPanel />}
              {runtimeControls && (
                <button
                  type="button"
                  onClick={() => navigate("#/settings")}
                  aria-label="打开设置"
                  aria-current={settingsActive ? "page" : undefined}
                  className={cn(
                    "flex size-11 items-center justify-center rounded-md transition-colors md:size-8",
                    settingsActive
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  title="设置"
                >
                  <HugeiconsIcon icon={Settings01Icon} className="size-4" />
                </button>
              )}
            </div>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-hidden" id="main-content">
          {children}
        </main>

        <MobileBottomNav activePage={activePage} />
      </div>
    </HeaderSlotContext.Provider>
  )
}
