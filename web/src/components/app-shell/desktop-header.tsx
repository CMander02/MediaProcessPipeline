import { HugeiconsIcon } from "@hugeicons/react"

import { navigate, type Route } from "@/lib/router"
import { cn } from "@/lib/utils"
import { PRIMARY_NAV_ITEMS } from "@/components/app-shell/navigation"

interface DesktopHeaderProps {
  activePage: Route["page"]
}

export function DesktopHeader({ activePage }: DesktopHeaderProps) {
  return (
    <div className="hidden shrink-0 items-center gap-5 md:flex" data-desktop-header>
      <button
        type="button"
        className="flex h-9 items-center gap-2 rounded-md px-1 text-foreground"
        onClick={() => navigate("#/files")}
        aria-label="打开 MPP 文件页"
      >
        <img src="/favicon.svg" className="size-5" alt="" aria-hidden="true" />
        <span className="text-sm font-semibold tracking-tight">MPP</span>
      </button>

      <nav className="flex items-center gap-1" aria-label="主导航">
        {PRIMARY_NAV_ITEMS.map((item) => {
          const active = activePage === item.page
          return (
            <button
              type="button"
              key={item.page}
              onClick={() => navigate(`#/${item.page}`)}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <HugeiconsIcon icon={item.icon} className="size-4" />
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>
    </div>
  )
}
