import { useEffect, useRef, useState } from "react"
import { subscribeTaskEvents, api, type Task, type TaskFlowSnapshot, type TaskTimelineEvent } from "@/lib/api"
import { navigate } from "@/lib/router"
import { STEP_NAME, usePipelineSteps } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { Tick02Icon, Loading03Icon, Cancel01Icon, ArrowLeft01Icon, CancelCircleIcon } from "@hugeicons/core-free-icons"

interface LogEntry {
  ts: string
  type: string
  detail: string
  level?: string
}

function timelineToLog(event: TaskTimelineEvent): LogEntry {
  const data = event.data
  const detail =
    event.message ||
    (typeof data.error === "string" ? data.error : "") ||
    (typeof data.message === "string" ? data.message : "")
  return {
    ts: event.timestamp.split("T")[1]?.slice(0, 8) ?? "",
    type: event.event_type,
    detail,
    level: event.level,
  }
}

export function ResultPageLive({ taskId }: { taskId: string }) {
  const pipelineSteps = usePipelineSteps()
  const [task, setTask] = useState<Task | null>(null)
  const [flow, setFlow] = useState<TaskFlowSnapshot | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  // Fetch initial task state
  useEffect(() => {
    Promise.all([api.tasks.get(taskId), api.tasks.timeline(taskId)])
      .then(([taskData, timeline]) => {
        setTask(taskData)
        setFlow(taskData.flow ?? null)
        setLogs(timeline.events.map(timelineToLog))
      })
      .catch(() => {})
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
      const level = typeof data.level === "string" ? data.level : undefined

      // Snapshot is a state-rebuild ping on (re)connect; merge it into task
      // state but don't add it to the visible event log (it isn't a real event).
      if (event.type !== "snapshot") {
        setLogs((prev) => [...prev, { ts, type: event.type, detail, level }].slice(-200))
      }

      // Update task state from snapshot or progress events
      if (data.status || data.current_step || data.completed_steps) {
        setTask((prev) => (prev ? { ...prev, ...data } as Task : prev))
      }
      if (data.flow && typeof data.flow === "object") {
        setFlow(data.flow as unknown as TaskFlowSnapshot)
      }
    })

    // Fallback poll every 10s (SSE is primary, this catches dropped connections)
    const interval = setInterval(async () => {
      try {
        const t = await api.tasks.get(taskId)
        setTask(t)
        if (t.status === "completed" || t.status === "failed" || t.status === "cancelled") {
          clearInterval(interval)
        }
      } catch {}
    }, 10000)

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
    <div className="h-full overflow-y-auto flex items-center justify-center">
      <div className="max-w-3xl w-full p-6 space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate("#/files")}>
            <HugeiconsIcon icon={ArrowLeft01Icon} className="h-4 w-4 mr-1" />
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
              <HugeiconsIcon icon={CancelCircleIcon} className="h-3.5 w-3.5 mr-1" />
              取消
            </Button>
          )}
        </div>

        {/* Pipeline steps - horizontal */}
        <div className="flex items-center gap-1">
          {pipelineSteps.map((step, i) => {
            const isCompleted = task?.completed_steps?.includes(step.id)
            const isCurrent = task?.current_step === step.id
            const isFailed = task?.status === "failed" && isCurrent
            return (
              <div key={step.id} className="flex items-center gap-1">
                <div className="flex items-center gap-1.5">
                  <div
                    className={cn(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                      isCompleted && "border-emerald-500 bg-emerald-500 text-white",
                      isCurrent && !isFailed && "border-blue-500 bg-blue-50 dark:bg-blue-950",
                      isFailed && "border-destructive bg-destructive/10",
                      !isCompleted && !isCurrent && "border-muted-foreground/30",
                    )}
                  >
                    {isCompleted ? (
                      <HugeiconsIcon icon={Tick02Icon} className="h-3 w-3" />
                    ) : isCurrent && !isFailed ? (
                      <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin text-blue-600" />
                    ) : isFailed ? (
                      <HugeiconsIcon icon={Cancel01Icon} className="h-3 w-3 text-destructive" />
                    ) : (
                      <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/20" />
                    )}
                  </div>
                  <span
                    className={cn(
                      "text-xs whitespace-nowrap",
                      isCompleted && "text-emerald-700 dark:text-emerald-400 font-medium",
                      isCurrent && !isFailed && "text-blue-700 dark:text-blue-400 font-medium",
                      isFailed && "text-destructive font-medium",
                      !isCompleted && !isCurrent && "text-muted-foreground",
                    )}
                  >
                    {step.name}
                  </span>
                </div>
                {i < pipelineSteps.length - 1 && (
                  <div
                    className={cn(
                      "h-px w-6 mx-1",
                      isCompleted ? "bg-emerald-400" : "bg-border",
                    )}
                  />
                )}
              </div>
            )
          })}
        </div>

        {flow && (
          <div className="space-y-2 border-y bg-muted/20 px-3 py-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-medium">{flow.label}</span>
              <span className="text-muted-foreground">{flow.platform}</span>
              <span className="text-blue-600">{flow.current_step_label ?? flow.current_step}</span>
              <span className="text-muted-foreground">{Math.round((flow.progress ?? 0) * 100)}%</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {flow.steps.map((step) => {
                const isDone = (flow.completed_steps ?? []).includes(step.id)
                const isCurrent = flow.current_step === step.id
                return (
                  <span
                    key={step.id}
                    className={cn(
                      "inline-flex h-5 items-center rounded px-1.5 text-[10px]",
                      isDone && "bg-emerald-50 text-emerald-700",
                      isCurrent && !isDone && "bg-blue-50 text-blue-700",
                      !isDone && !isCurrent && "bg-background text-muted-foreground",
                    )}
                  >
                    {step.label}
                  </span>
                )
              })}
            </div>
          </div>
        )}

        {/* Event log */}
        <div>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
            事件日志
          </h3>
          <div className="rounded-md border bg-muted/30 p-3 font-mono text-xs space-y-0.5 max-h-[60vh] overflow-y-auto">
            {logs.length === 0 && (
              <p className="text-muted-foreground py-2 text-center font-sans text-sm">
                等待事件...
              </p>
            )}
            {logs.map((e, i) => (
              <div key={i} className="flex gap-2 leading-5">
                <span className="text-muted-foreground shrink-0">{e.ts}</span>
                <span className={cn(
                  "shrink-0 w-20 truncate",
                  e.level === "error" && "text-destructive",
                  e.level === "warning" && "text-amber-600 dark:text-amber-400",
                  !e.level && "text-amber-600 dark:text-amber-400",
                )}>{e.type}</span>
                <span className="text-foreground truncate">{e.detail}</span>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Error display */}
        {task?.status === "failed" && task.error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {task.error}
          </div>
        )}
      </div>
    </div>
  )
}
