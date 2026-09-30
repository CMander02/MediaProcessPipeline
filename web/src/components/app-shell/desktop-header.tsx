import { HugeiconsIcon } from "@hugeicons/react"

import { libraryHash, navigate, type Route } from "@/lib/router"
import { AppMenu } from "@/components/app-shell/app-menu"
import { PRIMARY_NAV_ITEMS, titleBarButtonClass } from "@/components/app-shell/navigation"

interface DesktopHeaderProps {
  activePage: Route["page"]
}

/** MPP menu and main navigation. */
export function DesktopHeader({ activePage }: DesktopHeaderProps) {
  return (
    <div className="hidden shrink-0 items-center gap-2 md:flex" data-desktop-header>
      <AppMenu />

      <nav className="flex items-center gap-0.5" aria-label="主导航">
        {PRIMARY_NAV_ITEMS.map((item) => {
          const active = activePage === item.page
          return (
            <button
              type="button"
              key={item.page}
              onClick={() => navigate(item.page === "files" ? libraryHash() : `#/${item.page}`)}
              aria-current={active ? "page" : undefined}
              className={titleBarButtonClass(active)}
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
