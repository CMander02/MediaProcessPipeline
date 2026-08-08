import type { ReactNode } from "react"
import {
  Alert02Icon,
  FolderOpenIcon,
  Loading03Icon,
  SecurityLockIcon,
  WifiDisconnected01Icon,
} from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { cn } from "@/lib/utils"

type PageStateVariant = "loading" | "empty" | "error" | "offline" | "unauthorized"

interface PageStateProps {
  variant: PageStateVariant
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

const STATE_ICONS = {
  loading: Loading03Icon,
  empty: FolderOpenIcon,
  error: Alert02Icon,
  offline: WifiDisconnected01Icon,
  unauthorized: SecurityLockIcon,
}

export function PageState({ variant, title, description, action, className }: PageStateProps) {
  return (
    <div
      className={cn("flex min-h-48 flex-col items-center justify-center gap-3 px-6 py-10 text-center", className)}
      role={variant === "error" || variant === "offline" ? "alert" : "status"}
      aria-live={variant === "loading" ? "polite" : undefined}
    >
      <div className={cn(
        "flex size-11 items-center justify-center rounded-xl bg-muted text-muted-foreground",
        variant === "error" && "bg-destructive/10 text-destructive",
        variant === "offline" && "bg-amber-500/10 text-amber-700 dark:text-amber-300",
        variant === "unauthorized" && "bg-primary/10 text-primary",
      )}>
        <HugeiconsIcon
          icon={STATE_ICONS[variant]}
          className={cn("size-5", variant === "loading" && "animate-spin")}
        />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  )
}

export function LoadingState({ title = "正在加载", className }: { title?: string; className?: string }) {
  return <PageState variant="loading" title={title} className={className} />
}

export function EmptyState({
  title,
  description,
  action,
  className,
}: Omit<PageStateProps, "variant">) {
  return <PageState variant="empty" title={title} description={description} action={action} className={className} />
}

export function ErrorState(props: Omit<PageStateProps, "variant">) {
  return <PageState variant="error" {...props} />
}

export function OfflineState(props: Omit<PageStateProps, "variant">) {
  return <PageState variant="offline" {...props} />
}

export function UnauthorizedState(props: Omit<PageStateProps, "variant">) {
  return <PageState variant="unauthorized" {...props} />
}
