import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { StepProgress } from "@/components/step-progress"
import { STATUS_CONFIG } from "@/lib/constants"
import { cn } from "@/lib/utils"
import type { Task } from "@/lib/api"

function formatSource(source: string): string {
  // Show just the filename for local paths
  const parts = source.replace(/\\/g, "/").split("/")
  const name = parts.at(-1) ?? source
  return name.length > 60 ? `\u2026${name.slice(-57)}` : name
}

function formatDuration(created: string, completed: string | null): string {
  if (!completed) {
    const secs = (Date.now() - new Date(created).getTime()) / 1000
    if (secs < 60) return `${Math.floor(secs)}s`
    return `${(secs / 60).toFixed(1)}m`
  }
  const secs = (new Date(completed).getTime() - new Date(created).getTime()) / 1000
  if (secs < 60) return `${Math.floor(secs)}s`
  return `${(secs / 60).toFixed(1)}m`
}

export function TaskCard({ task, onClick }: { task: Task; onClick?: () => void }) {
  const cfg = STATUS_CONFIG[task.status] ?? STATUS_CONFIG.cancelled
  const pct = Math.round(task.progress * 100)
  const isActive = task.status === "processing" || task.status === "queued"

  return (
    <Card
      className={cn(
        "transition-all cursor-pointer hover:shadow-md",
        isActive && "border-blue-200 bg-blue-50/30",
        task.status === "failed" && "border-red-200 bg-red-50/30",
      )}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onClick?.()}
    >
      <CardContent className="p-4 space-y-3">
        {/* Header: status + source + duration */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <span className={cn("h-2 w-2 rounded-full shrink-0", cfg.dot)} />
            <span className="text-sm font-medium truncate" title={task.source}>
              {formatSource(task.source)}
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Badge variant={isActive ? "default" : "secondary"} className="text-xs">
              {cfg.label}
            </Badge>
            <span className="text-xs text-muted-foreground tabular-nums">
              {formatDuration(task.created_at, task.completed_at)}
            </span>
          </div>
        </div>

        {/* Progress bar for active tasks */}
        {isActive && (
          <div className="space-y-2">
            <Progress value={pct} className="h-1.5" />
            <StepProgress task={task} />
          </div>
        )}

        {/* Error message */}
        {task.error && (
          <p className="text-xs text-destructive truncate" title={task.error}>
            {task.error}
          </p>
        )}

        {/* ID */}
        <p className="text-xs text-muted-foreground font-mono">{task.id.slice(0, 8)}</p>
      </CardContent>
    </Card>
  )
}
