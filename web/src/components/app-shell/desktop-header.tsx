import { HugeiconsIcon } from "@hugeicons/react"

import { libraryHash, navigate, type Route } from "@/lib/router"
import { cn } from "@/lib/utils"
import { AppMenu } from "@/components/app-shell/app-menu"
import { PRIMARY_NAV_ITEMS } from "@/components/app-shell/navigation"

interface DesktopHeaderProps {
  activePage: Route["page"]
}

/** MPP menu and main navigation; the navigation steps aside while a page fills the title bar. */
export function DesktopHeader({ activePage }: DesktopHeaderProps) {
  return (
    <div className="hidden shrink-0 items-center gap-2 md:flex" data-desktop-header>
      <AppMenu />

      <nav className="flex items-center gap-0.5 group-has-[[data-header-slot]>*]/header:hidden" aria-label="主导航">
        {PRIMARY_NAV_ITEMS.map((item) => {
          const active = activePage === item.page
          return (
            <button
              type="button"
              key={item.page}
              onClick={() => navigate(item.page === "files" ? libraryHash() : `#/${item.page}`)}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[13px] font-medium transition-colors",
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
