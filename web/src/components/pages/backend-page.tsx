import { useCallback, useEffect, useMemo, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Activity01Icon,
  AiBrain01Icon,
  Clock01Icon,
  ComputerTerminal01Icon,
  Loading03Icon,
  RefreshIcon,
  ServerStack01Icon,
  Task01Icon,
} from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/page-state"
import { api, subscribeAllEvents, type HealthInfo, type Settings, type Task, type TaskStats } from "@/lib/api"
import { navigate } from "@/lib/router"
import { cn } from "@/lib/utils"

interface ServiceEvent {
  id: string
  taskId: string
  type: string
  message: string
  timestamp: string
}

const activeStatuses = new Set<Task["status"]>(["queued", "processing", "paused"])

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "--:--:--"
  return date.toLocaleTimeString("zh-CN", { hour12: false })
}

function statusLabel(status: Task["status"]) {
  if (status === "processing") return "处理中"
  if (status === "queued") return "排队中"
  if (status === "paused") return "已暂停"
  return status
}

function eventMessage(type: string, data: Record<string, unknown>) {
  if (typeof data.message === "string" && data.message) return data.message
  if (typeof data.error === "string" && data.error) return data.error
  if (typeof data.step === "string" && data.step) {
    const progress = Number(data.progress)
    return Number.isFinite(progress) ? `${data.step} · ${Math.round(progress * 100)}%` : data.step
  }
  return type
}

export function BackendPage() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [stats, setStats] = useState<TaskStats | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [events, setEvents] = useState<ServiceEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const refresh = useCallback(async (showBusy = false) => {
    if (showBusy) setRefreshing(true)
    try {
      const [healthInfo, taskStats, queued, processing, paused, runtimeSettings] = await Promise.all([
        api.health(),
        api.tasks.stats(),
        api.tasks.list("queued", 50),
        api.tasks.list("processing", 50),
        api.tasks.list("paused", 50),
        api.settings.get(),
      ])
      setHealth(healthInfo)
      setStats(taskStats)
      setTasks(
        [...processing, ...queued, ...paused]
          .filter((task, index, all) => activeStatuses.has(task.status) && all.findIndex((item) => item.id === task.id) === index)
          .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)),
      )
      setSettings(runtimeSettings)
      setError(null)
      setLastUpdated(new Date())
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      if (showBusy) setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 10_000)
    const unsubscribe = subscribeAllEvents((event) => {
      setEvents((current) => [{
        id: `${event.timestamp}-${event.task_id}-${event.type}`,
        taskId: event.task_id,
        type: event.type,
        message: eventMessage(event.type, event.data),
        timestamp: event.timestamp,
      }, ...current].slice(0, 50))
    })
    return () => {
      window.clearInterval(timer)
      unsubscribe()
    }
  }, [refresh])

  const processingCount = stats?.processing ?? tasks.filter((task) => task.status === "processing").length
  const activeCount = (stats?.processing ?? 0) + (stats?.queued ?? 0) + (stats?.paused ?? 0)
  const serviceHealthy = health?.status === "ok" || health?.status === "healthy"
  const modelState = processingCount > 0 ? "占用中" : "空闲"
  const asrName = settings?.asr_provider || "未配置"
  const llmName = settings?.llm_provider || "未配置"
  const lastUpdateText = useMemo(
    () => lastUpdated?.toLocaleTimeString("zh-CN", { hour12: false }) ?? "等待首次检查",
    [lastUpdated],
  )

  const metrics = [
    { label: "服务状态", value: serviceHealthy ? "运行正常" : "检查中", detail: health?.service ?? "MediaProcessPipeline", icon: ServerStack01Icon },
    { label: "活动任务", value: String(activeCount), detail: `${processingCount} 个正在处理`, icon: Task01Icon },
    { label: "模型占用", value: modelState, detail: "单 GPU 工作器", icon: AiBrain01Icon },
    { label: "服务版本", value: health?.version ?? "--", detail: `更新于 ${lastUpdateText}`, icon: Clock01Icon },
  ]

  return (
    <section className="h-full min-h-0 overflow-y-auto bg-background">
      <div className="mx-auto w-full max-w-[1680px] space-y-4 p-3 pb-8 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-lg font-semibold md:text-xl">
              <HugeiconsIcon icon={ComputerTerminal01Icon} className="h-5 w-5 text-primary" />
              后端运行中心
            </div>
            <p className="mt-1 text-sm text-muted-foreground">查看服务健康、任务队列、模型占用和实时事件。</p>
          </div>
          <Button className="h-11 md:h-9" variant="outline" onClick={() => void refresh(true)} disabled={refreshing}>
            <HugeiconsIcon icon={RefreshIcon} className={cn("h-4 w-4", refreshing && "animate-spin")} />
            刷新状态
          </Button>
        </header>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive" role="alert">
            服务状态读取失败：{error}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {metrics.map((metric) => (
            <Card key={metric.label} className="gap-3 py-4 shadow-none">
              <CardHeader className="flex flex-row items-center justify-between px-4 pb-0">
                <CardTitle className="text-xs font-medium text-muted-foreground sm:text-sm">{metric.label}</CardTitle>
                <HugeiconsIcon icon={metric.icon} className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent className="px-4">
                <div className="text-xl font-semibold sm:text-2xl">{metric.value}</div>
                <p className="mt-1 truncate text-xs text-muted-foreground" title={metric.detail}>{metric.detail}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
          <Card className="min-h-[280px] gap-3 py-4 shadow-none">
            <CardHeader className="flex flex-row items-center justify-between px-4 pb-0">
              <div>
                <CardTitle className="text-base">任务队列</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">排队、处理和暂停中的任务</p>
              </div>
              <span className="rounded-md bg-muted px-2 py-1 text-xs tabular-nums">{tasks.length}</span>
            </CardHeader>
            <CardContent className="px-4">
              {tasks.length === 0 ? (
                <EmptyState title="当前队列为空" description="新任务提交后会显示在这里。" className="min-h-[190px]" />
              ) : (
                <div className="space-y-2">
                  {tasks.map((task) => (
                    <button
                      key={task.id}
                      type="button"
                      onClick={() => navigate(`#/result/task/${task.id}`)}
                      className="flex min-h-14 w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <span className={cn("h-2 w-2 shrink-0 rounded-full", task.status === "processing" ? "bg-primary" : "bg-muted-foreground/50")} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{task.source.split(/[/\\]/).pop() || task.id}</span>
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{task.message || task.current_step || "等待处理"}</span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block text-xs font-medium">{statusLabel(task.status)}</span>
                        <span className="mt-0.5 block text-xs tabular-nums text-muted-foreground">{Math.round((task.progress ?? 0) * 100)}%</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="gap-3 py-4 shadow-none">
            <CardHeader className="px-4 pb-0">
              <CardTitle className="text-base">模型运行</CardTitle>
              <p className="text-xs text-muted-foreground">当前处理配置和工作器占用</p>
            </CardHeader>
            <CardContent className="space-y-3 px-4">
              <div className="flex items-center justify-between rounded-lg border px-3 py-3">
                <span className="text-sm text-muted-foreground">ASR</span>
                <span className="max-w-[60%] truncate text-sm font-medium" title={asrName}>{asrName}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-3 py-3">
                <span className="text-sm text-muted-foreground">LLM</span>
                <span className="max-w-[60%] truncate text-sm font-medium" title={llmName}>{llmName}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-3 py-3">
                <span className="text-sm text-muted-foreground">工作器</span>
                <span className="inline-flex items-center gap-1.5 text-sm font-medium">
                  {processingCount > 0 && <HugeiconsIcon icon={Loading03Icon} className="h-3.5 w-3.5 animate-spin" />}
                  {modelState}
                </span>
              </div>
              <div className="rounded-lg bg-muted/40 px-3 py-2 text-xs leading-5 text-muted-foreground">
                服务进程由 Windows 或 Linux 启动脚本管理，网页持续显示运行状态。
              </div>
            </CardContent>
          </Card>
        </div>

        <Card className="gap-3 py-4 shadow-none">
          <CardHeader className="flex flex-row items-center justify-between px-4 pb-0">
            <div>
              <CardTitle className="text-base">实时事件</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">本次页面打开后收到的任务事件</p>
            </div>
            <HugeiconsIcon icon={Activity01Icon} className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent className="px-4">
            {events.length === 0 ? (
              <div className="flex min-h-28 items-center justify-center text-sm text-muted-foreground">等待任务事件…</div>
            ) : (
              <div className="max-h-72 overflow-y-auto rounded-lg border">
                {events.map((event) => (
                  <button
                    key={event.id}
                    type="button"
                    onClick={() => event.taskId && navigate(`#/result/task/${event.taskId}`)}
                    className="grid min-h-11 w-full grid-cols-[4.5rem_minmax(0,1fr)] gap-2 border-b px-3 py-2 text-left text-xs last:border-b-0 hover:bg-muted/40 sm:grid-cols-[4.5rem_7rem_minmax(0,1fr)]"
                  >
                    <span className="tabular-nums text-muted-foreground">{formatTime(event.timestamp)}</span>
                    <span className="hidden truncate text-muted-foreground sm:block">{event.type}</span>
                    <span className="min-w-0 break-words">{event.message}</span>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  )
}
