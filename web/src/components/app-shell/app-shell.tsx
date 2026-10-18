import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react"
import { ArrowLeft01Icon, Cancel01Icon, Menu01Icon, Search01Icon, Settings01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { ActivityPanel } from "@/components/activity/activity-panel"
import { DesktopHeader } from "@/components/app-shell/desktop-header"
import { HistoryButtons } from "@/components/app-shell/history-buttons"
import { MobileBottomNav } from "@/components/app-shell/mobile-bottom-nav"
import { PAGE_TITLES } from "@/components/app-shell/navigation"
import { cssColorToHex, desktopApp } from "@/lib/desktop-bridge"
import { navigate, type Route } from "@/lib/router"
import { openSearch } from "@/lib/search-store"
import { cn } from "@/lib/utils"

interface AppShellProps {
  activePage: Route["page"]
  toolbar?: ReactNode
  crumbs?: ReactNode
  trailing?: ReactNode
  children: ReactNode
  runtimeControls?: boolean
  mobileDetail?: boolean
  onMobileBack?: () => void
  mobileBackLabel?: string
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

export function AppShell({ activePage, toolbar, crumbs, trailing, children, runtimeControls = true, mobileDetail = false, onMobileBack, mobileBackLabel = "返回" }: AppShellProps) {
  const settingsActive = activePage === "settings"
  const [mobileToolsLocation, setMobileToolsLocation] = useState<string | null>(null)
  const mobileToolsOpen = mobileToolsLocation === window.location.hash
  const header = useRef<HTMLElement>(null)
  useTitleBarColors(header)

  return (
    <>
      <div data-mobile-detail={mobileDetail || undefined} className="relative flex h-screen supports-[height:100dvh]:h-dvh flex-col overflow-hidden bg-background">
        {mobileDetail && !mobileToolsOpen && <nav data-mobile-overlay aria-label="内容导航" className="pointer-events-none absolute inset-x-0 top-0 z-40 flex justify-between bg-gradient-to-b from-black/45 to-transparent px-2 pb-3 pt-[max(0.25rem,var(--mpp-safe-top))] text-white md:hidden">
          <button type="button" aria-label={mobileBackLabel} onClick={onMobileBack} className="pointer-events-auto flex size-11 items-center justify-center rounded-full bg-black/25"><HugeiconsIcon icon={ArrowLeft01Icon} className="size-6" /></button>
          <button type="button" aria-label="展开顶部工具栏" aria-expanded={false} onClick={() => setMobileToolsLocation(window.location.hash)} className="pointer-events-auto flex size-11 items-center justify-center rounded-full bg-black/25"><HugeiconsIcon icon={Menu01Icon} className="size-5" /></button>
        </nav>}
        <header ref={header} className={cn("shrink-0 border-b bg-card text-foreground pt-[var(--mpp-safe-top)]", mobileDetail && !mobileToolsOpen && "max-md:hidden")}>
          {/* One row on desktop (the window title bar in the desktop app); two rows on phones. */}
          <div
            className={cn(
              "flex min-h-14 flex-wrap items-center gap-2 px-3 py-2",
              "md:h-11 md:min-h-0 md:flex-nowrap md:gap-1.5 md:py-0 md:pl-2 md:pr-2",
              desktopApp && "app-drag",
            )}
            style={desktopApp ? DESKTOP_TITLE_BAR : undefined}
          >
            <HistoryButtons className="hidden md:flex" />
            {mobileDetail && <button type="button" aria-label="收起顶部工具栏" aria-expanded={mobileToolsOpen} onClick={() => setMobileToolsLocation(null)} className="flex size-11 shrink-0 items-center justify-center rounded-md md:hidden"><HugeiconsIcon icon={Cancel01Icon} className="size-5" /></button>}
            <DesktopHeader activePage={activePage} />
            {crumbs && <div className="hidden min-w-0 max-w-72 md:block">{crumbs}</div>}

            <div className="flex min-w-0 flex-1 items-center gap-2 md:hidden" data-mobile-header>
              <img src="/favicon.svg" className="size-5 shrink-0" alt="" aria-hidden="true" />
              {crumbs ?? <h1 className="truncate text-base font-semibold tracking-tight">{PAGE_TITLES[activePage]}</h1>}
            </div>

            {toolbar ? (
              <div className="order-last w-full min-w-0 pt-1 md:order-none md:w-auto md:flex-1 md:pt-0">
                {toolbar}
              </div>
            ) : (
              <div className="hidden min-w-0 flex-1 md:block" />
            )}

            <div className="ml-auto flex shrink-0 items-center gap-1">
              {trailing}
              {/* Phones only; on desktop Ctrl+K opens the same search. */}
              {runtimeControls && !toolbar && (
                <button
                  type="button"
                  onClick={openSearch}
                  aria-label="搜索"
                  title="搜索标题、说话人和字幕"
                  className="flex size-11 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
                >
                  <HugeiconsIcon icon={Search01Icon} className="size-4" />
                </button>
              )}
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

        <main className={cn("min-h-0 flex-1 overflow-hidden", mobileDetail && "max-md:pt-[var(--mpp-safe-top)]")} id="main-content">
          {children}
        </main>

        <div className={mobileDetail ? "hidden md:contents" : "contents"}><MobileBottomNav activePage={activePage} /></div>
      </div>
    </>
  )
}
