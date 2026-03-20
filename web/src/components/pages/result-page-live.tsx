import { useEffect, useRef, useState } from "react"
import { subscribeTaskEvents, api, type Task } from "@/lib/api"
import { navigate } from "@/lib/router"
import { PIPELINE_STEPS, STEP_NAME } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Check, Loader2, X, ArrowLeft, Ban } from "lucide-react"

interface LogEntry {
  ts: string
  type: string
  detail: string
}

export function ResultPageLive({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<Task | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  // Fetch initial task state
  useEffect(() => {
    api.tasks.get(taskId).then(setTask).catch(() => {})
  }, [taskId])

  // Subscribe to SSE events
  useEffect(() => {
    const unsub = subscribeTaskEvents(taskId, (event) => {
      const ts = event.timestamp.split("T")[1]?.slice(0, 8) ?? ""
      const data = event.data
      let detail = ""
      if (data.step) {
        const stepName = STEP_NAME[data.step as string] ?? data.step
        const pct = data.progress ? ` ${Math.round(Number(data.progress) * 100)}%` : ""
        detail = `${stepName}${pct}`
      } else if (data.error) {
        detail = String(data.error).slice(0, 200)
      } else if (data.message) {
        detail = String(data.message)
      } else if (data.output_dir) {
        detail = `归档完成`
      }

      setLogs((prev) => [...prev, { ts, type: event.type, detail }])

      // Update task state from snapshot or progress events
      if (data.status || data.current_step || data.completed_steps) {
        setTask((prev) => (prev ? { ...prev, ...data } as Task : prev))
      }
    })

    // Also poll task state every 3s for robustness
    const interval = setInterval(async () => {
      try {
        const t = await api.tasks.get(taskId)
        setTask(t)
        if (t.status === "completed" || t.status === "failed" || t.status === "cancelled") {
          clearInterval(interval)
        }
      } catch {}
    }, 3000)

    return () => {
      unsub()
      clearInterval(interval)
    }
  }, [taskId])

  // Auto-scroll log
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [logs])

  // Transition to completed viewer when task finishes
  useEffect(() => {
    if (task?.status === "completed" && task.result?.output_dir) {
      const outputDir = String(task.result.output_dir)
      // Small delay so user sees the "completed" state
      const timer = setTimeout(() => {
        navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}`, { replace: true })
      }, 1500)
      return () => clearTimeout(timer)
    }
  }, [task?.status, task?.result])

  const handleCancel = async () => {
    try {
      await api.tasks.cancel(taskId)
    } catch {}
  }

  const isTerminal = task?.status === "completed" || task?.status === "failed" || task?.status === "cancelled"

  return (
    <div className="flex h-full flex-col p-6 gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate("#/files")}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          返回
        </Button>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-medium truncate">
            {task?.source ? task.source.split(/[/\\]/).pop() : `任务 ${taskId.slice(0, 8)}`}
          </h2>
          {task?.status && (
            <p className={cn(
              "text-xs",
              task.status === "processing" && "text-blue-600",
              task.status === "completed" && "text-emerald-600",
              task.status === "failed" && "text-destructive",
              task.status === "queued" && "text-amber-600",
              task.status === "cancelled" && "text-muted-foreground",
            )}>
              {task.status === "processing" ? "处理中..." : task.status === "completed" ? "已完成" : task.status === "failed" ? "失败" : task.status === "queued" ? "排队中" : "已取消"}
              {task.message && ` — ${task.message}`}
            </p>
          )}
        </div>
        {task && !isTerminal && (
          <Button variant="outline" size="sm" onClick={handleCancel}>
            <Ban className="h-3.5 w-3.5 mr-1" />
            取消
          </Button>
        )}
      </div>

      {/* Vertical step progress */}
      <div className="flex gap-6 flex-1 min-h-0">
        <div className="shrink-0 w-48">
          <div className="space-y-1">
            {PIPELINE_STEPS.map((step, i) => {
              const isCompleted = task?.completed_steps?.includes(step.id)
              const isCurrent = task?.current_step === step.id
              const isFailed = task?.status === "failed" && isCurrent
              return (
                <div key={step.id} className="flex items-start gap-2.5">
                  {/* Dot + line */}
                  <div className="flex flex-col items-center">
                    <div
                      className={cn(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                        isCompleted && "border-emerald-500 bg-emerald-500 text-white",
                        isCurrent && !isFailed && "border-blue-500 bg-blue-50 dark:bg-blue-950",
                        isFailed && "border-destructive bg-destructive/10",
                        !isCompleted && !isCurrent && "border-muted-foreground/30",
                      )}
                    >
                      {isCompleted ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : isCurrent && !isFailed ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />
                      ) : isFailed ? (
                        <X className="h-3.5 w-3.5 text-destructive" />
                      ) : (
                        <span className="h-2 w-2 rounded-full bg-muted-foreground/20" />
                      )}
                    </div>
                    {i < PIPELINE_STEPS.length - 1 && (
                      <div
                        className={cn(
                          "w-0.5 h-4",
                          isCompleted ? "bg-emerald-400" : "bg-border",
                        )}
                      />
                    )}
                  </div>
                  <span
                    className={cn(
                      "text-sm leading-6",
                      isCompleted && "text-emerald-700 dark:text-emerald-400 font-medium",
                      isCurrent && !isFailed && "text-blue-700 dark:text-blue-400 font-medium",
                      isFailed && "text-destructive font-medium",
                      !isCompleted && !isCurrent && "text-muted-foreground",
                    )}
                  >
                    {step.name}
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        {/* Event log */}
        <div className="flex-1 min-w-0 flex flex-col">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
            事件日志
          </h3>
          <ScrollArea className="flex-1 rounded-md border bg-muted/30">
            <div className="p-3 font-mono text-xs space-y-0.5">
              {logs.length === 0 && (
                <p className="text-muted-foreground py-4 text-center font-sans text-sm">
                  等待事件...
                </p>
              )}
              {logs.map((e, i) => (
                <div key={i} className="flex gap-2 leading-5">
                  <span className="text-muted-foreground shrink-0">{e.ts}</span>
                  <span className="text-amber-600 dark:text-amber-400 shrink-0 w-16">{e.type}</span>
                  <span className="text-foreground truncate">{e.detail}</span>
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        </div>
      </div>

      {/* Error display */}
      {task?.status === "failed" && task.error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {task.error}
        </div>
      )}
    </div>
  )
}
