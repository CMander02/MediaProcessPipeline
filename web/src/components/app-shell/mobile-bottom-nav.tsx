import { HugeiconsIcon } from "@hugeicons/react"

import { navigate, type Route } from "@/lib/router"
import { cn } from "@/lib/utils"
import { PRIMARY_NAV_ITEMS } from "@/components/app-shell/navigation"

interface MobileBottomNavProps {
  activePage: Route["page"]
}

export function MobileBottomNav({ activePage }: MobileBottomNavProps) {
  return (
    <nav
      className="grid shrink-0 grid-cols-3 border-t bg-card px-2 pb-[max(0.5rem,var(--mpp-safe-bottom))] pt-1.5 md:hidden"
      data-mobile-bottom-nav
      aria-label="主导航"
    >
      {PRIMARY_NAV_ITEMS.map((item) => {
        const active = activePage === item.page
        return (
          <button
            type="button"
            key={item.page}
            onClick={() => navigate(`#/${item.page}`)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "mx-auto flex min-h-11 w-full max-w-28 flex-col items-center justify-center gap-0.5 rounded-lg text-[0.6875rem] font-medium transition-colors",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground active:bg-muted active:text-foreground",
            )}
          >
            <HugeiconsIcon icon={item.icon} className="size-5" />
            <span>{item.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
