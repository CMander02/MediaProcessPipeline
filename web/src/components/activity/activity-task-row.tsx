import { HugeiconsIcon } from "@hugeicons/react"
import { Delete01Icon, Loading03Icon, PauseIcon, PlayIcon, RefreshIcon } from "@hugeicons/core-free-icons"

import type { Task } from "@/lib/api"
import { STEP_NAME } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { taskDisplayTitle } from "@/lib/task-display"
import { Button } from "@/components/ui/button"

export type TaskRowAction = "pause" | "resume" | "retry" | "delete"

function shortTime(value: string | null | undefined): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (part: number) => String(part).padStart(2, "0")
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function taskDetail(task: Task): string {
  const pct = Math.round((task.progress ?? 0) * 100)
  const step = task.current_step ? (STEP_NAME[task.current_step] ?? task.current_step) : null
  switch (task.status) {
    case "processing":
      return `${step ?? "处理中"} · ${pct}%`
    case "queued":
    case "pending":
      return "排队中"
    case "paused":
      return `暂停于 ${shortTime(task.updated_at)}${pct ? ` · ${pct}%` : ""}`
    case "failed":
      return task.error || task.message || "处理失败"
    case "cancelled":
      return `已取消 · ${shortTime(task.updated_at)}`
    case "completed":
      return `完成于 ${shortTime(task.completed_at ?? task.updated_at)}`
    default:
      return ""
  }
}

interface ActivityTaskRowProps {
  task: Task
  busy?: boolean
  /** Set when this failed run's source already has a finished archive. */
  note?: string
  onOpen: () => void
  onAction?: (action: TaskRowAction) => void
}

/** One task, the same everywhere it is listed: activity panel and the backend page. */
export function ActivityTaskRow({ task, busy = false, note, onOpen, onAction }: ActivityTaskRowProps) {
  const pct = Math.round((task.progress ?? 0) * 100)
  const running = task.status === "processing"
  const failed = task.status === "failed"
  const stopped = failed || task.status === "cancelled"
  const actions: Array<{ action: TaskRowAction; label: string; icon: typeof PauseIcon }> = []
  if (running || task.status === "queued") actions.push({ action: "pause", label: "暂停", icon: PauseIcon })
  if (task.status === "paused") actions.push({ action: "resume", label: "继续", icon: PlayIcon })
  if (stopped) actions.push({ action: "retry", label: "从断点重试", icon: RefreshIcon })
  if (task.status !== "completed") actions.push({ action: "delete", label: "删除", icon: Delete01Icon })

  return (
    <div className="group flex items-center gap-1 rounded-md px-2 py-1.5 transition-colors hover:bg-muted/60">
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 items-start gap-2.5 rounded px-1 py-0.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="mt-1.5 flex size-2 shrink-0 items-center justify-center">
          <span className={cn(
            "size-2 rounded-full",
            running ? "animate-pulse bg-primary" : task.status === "paused" ? "bg-amber-500"
              : failed ? "bg-destructive" : task.status === "completed" ? "bg-emerald-500" : "bg-muted-foreground/50",
          )} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium" title={`${taskDisplayTitle(task)}\n${task.source}`}>
            {taskDisplayTitle(task)}
          </span>
          <span
            className={cn("mt-0.5 block truncate text-xs tabular-nums", failed ? "text-destructive" : "text-muted-foreground")}
            title={failed ? (task.error ?? undefined) : undefined}
          >
            {taskDetail(task)}
            {note && <span className="text-muted-foreground"> · {note}</span>}
          </span>
          {(running || task.status === "paused") && (
            <span className="mt-1 block h-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
              <span
                className={cn("block h-full rounded-full transition-[width]", running ? "bg-primary" : "bg-amber-500")}
                style={{ width: `${pct}%` }}
              />
            </span>
          )}
        </span>
      </button>

      {onAction && actions.length > 0 && (
        <div className="flex shrink-0 items-center gap-0.5">
          {actions.map(({ action, label, icon }) => (
            <Button
              key={action}
              type="button"
              size="icon-sm"
              variant="ghost"
              title={label}
              aria-label={`${label}「${taskDisplayTitle(task)}」`}
              disabled={busy}
              onClick={() => onAction(action)}
              className={cn("size-7", action === "delete" && "text-muted-foreground hover:text-destructive")}
            >
              <HugeiconsIcon
                icon={busy && action !== "delete" ? Loading03Icon : icon}
                className={cn("size-3.5", busy && action !== "delete" && "animate-spin")}
              />
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
