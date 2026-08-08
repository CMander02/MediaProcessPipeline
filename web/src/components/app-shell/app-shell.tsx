import type { ReactNode } from "react"
import { Settings01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { DesktopHeader } from "@/components/app-shell/desktop-header"
import { MobileBottomNav } from "@/components/app-shell/mobile-bottom-nav"
import { PAGE_TITLES } from "@/components/app-shell/navigation"
import { TaskQueueDropdown } from "@/components/task-queue-dropdown"
import { navigate, type Route } from "@/lib/router"
import { cn } from "@/lib/utils"

interface AppShellProps {
  activePage: Route["page"]
  toolbar?: ReactNode
  children: ReactNode
}

export function AppShell({ activePage, toolbar, children }: AppShellProps) {
  const settingsActive = activePage === "settings"

  return (
    <div className="flex h-screen supports-[height:100dvh]:h-dvh flex-col overflow-hidden bg-background">
      <header className="shrink-0 border-b bg-card pt-[env(safe-area-inset-top)]">
        <div className="flex min-h-14 flex-wrap items-center gap-2 px-3 py-2 sm:px-4">
          <DesktopHeader activePage={activePage} />

          <div className="flex min-w-0 flex-1 items-center gap-2 md:hidden">
            <img src="/favicon.svg" className="size-5 shrink-0" alt="" aria-hidden="true" />
            <h1 className="truncate text-base font-semibold tracking-tight">{PAGE_TITLES[activePage]}</h1>
          </div>

          {toolbar ? (
            <div className="order-last w-full min-w-0 pt-1 md:order-none md:ml-2 md:flex-1 md:pt-0">
              {toolbar}
            </div>
          ) : (
            <div className="hidden min-w-0 flex-1 md:block" />
          )}

          <div className="ml-auto flex shrink-0 items-center gap-1">
            <TaskQueueDropdown />
            <button
              type="button"
              onClick={() => navigate("#/settings")}
              aria-label="打开设置"
              aria-current={settingsActive ? "page" : undefined}
              className={cn(
                "flex size-11 items-center justify-center rounded-md transition-colors md:size-9",
                settingsActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              title="设置"
            >
              <HugeiconsIcon icon={Settings01Icon} className="size-4" />
            </button>
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-hidden" id="main-content">
        {children}
      </main>

      <MobileBottomNav activePage={activePage} />
    </div>
  )
}
