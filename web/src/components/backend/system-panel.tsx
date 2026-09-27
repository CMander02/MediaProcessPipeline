import { useEffect, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Alert02Icon,
  ArrowRight01Icon,
  GpuIcon,
  HardDriveIcon,
  Loading03Icon,
  Task01Icon,
} from "@hugeicons/core-free-icons"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { api, type SystemError, type SystemStatus } from "@/lib/api"
import { STEP_NAME } from "@/lib/constants"
import { buildHash, navigate } from "@/lib/router"
import { cn } from "@/lib/utils"

const GB = 1024 ** 3
// Below this much free space, downloads and long recordings start failing.
const LOW_DISK_BYTES = 20 * GB

function gigabytes(bytes: number): string {
  if (bytes >= 1024 * GB) return `${(bytes / 1024 / GB).toFixed(2)} TB`
  return `${(bytes / GB).toFixed(bytes >= 100 * GB ? 0 : 1)} GB`
}

function percent(part: number | null, whole: number | null): number | null {
  return part !== null && whole ? Math.round((part / whole) * 100) : null
}

function uptime(startedAt: string | null): string | null {
  if (!startedAt) return null
  const minutes = Math.floor((Date.now() - Date.parse(startedAt)) / 60_000)
  if (!Number.isFinite(minutes) || minutes < 0) return null
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  return hours < 48 ? `${hours} 小时` : `${Math.floor(hours / 24)} 天`
}

function errorTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
  return date.toDateString() === new Date().toDateString() ? time : `昨天 ${time}`
}

/** What to put in the log filter to find this error again. */
function logQuery(error: SystemError): string {
  return error.event && error.event !== "log" ? error.event : error.message.slice(0, 60)
}

function Meter({ value, warn }: { value: number | null; warn: boolean }) {
  return (
    <Progress
      value={value ?? 0}
      className={cn("h-1.5", warn && "[&>[data-slot=progress-indicator]]:bg-amber-500")}
    />
  )
}

function GpuCard({ status }: { status: SystemStatus }) {
  const { gpu, models, workers } = status
  const device = gpu.devices[0]
  const used = device?.memory_used_mb ?? null
  const total = device?.memory_total_mb ?? null
  const usedPercent = percent(used, total)
  // Memory is nearly full although MPP holds nothing: something else is using the card.
  const crowded = usedPercent !== null && usedPercent >= 85 && models.length === 0 && !workers.gpu.busy

  return (
    <Card className="gap-3 shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HugeiconsIcon icon={GpuIcon} className="size-4 text-muted-foreground" />
          GPU
        </CardTitle>
        <CardDescription className="truncate">{device?.name ?? "没有检测到 NVIDIA GPU"}</CardDescription>
        {device && (
          <CardAction className="text-xs tabular-nums text-muted-foreground">
            {device.utilization !== null && `利用率 ${device.utilization}%`}
            {device.temperature !== null && ` · ${device.temperature}°C`}
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {device ? (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-muted-foreground">显存</span>
              <span className="tabular-nums">
                {used !== null && total ? `${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} GB` : "--"}
                {usedPercent !== null && <span className="ml-1.5 text-muted-foreground">{usedPercent}%</span>}
              </span>
            </div>
            <Meter value={usedPercent} warn={(usedPercent ?? 0) >= 90} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{gpu.error ?? "语音识别和人声分离会改用 CPU，速度会慢很多。"}</p>
        )}

        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">MPP 加载的模型</span>
          {models.length ? (
            <ul className="flex flex-col gap-1 text-sm">
              {models.map((model) => (
                <li key={`${model.kind}-${model.name}-${model.pid ?? ""}`} className="flex min-w-0 items-baseline gap-2">
                  <span className="shrink-0 text-xs text-muted-foreground">{model.kind}</span>
                  <span className="min-w-0 truncate font-mono text-xs" title={model.name}>{model.name}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">没有。队列空了以后 MPP 会自动释放显存。</p>
          )}
          {gpu.mpp_reserved_mb ? (
            <p className="text-xs text-muted-foreground">PyTorch 占用 {(gpu.mpp_reserved_mb / 1024).toFixed(1)} GB</p>
          ) : null}
        </div>

        {crowded && (
          <p className="flex items-start gap-1.5 rounded-md bg-amber-500/10 px-2.5 py-2 text-xs text-amber-700 dark:text-amber-400">
            <HugeiconsIcon icon={Alert02Icon} className="mt-px size-3.5 shrink-0" />
            显存大多被其他程序占用。处理视频前关掉它们，否则识别会变慢或因显存不足失败。
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function WorkerCard({ status, onShowQueue }: { status: SystemStatus; onShowQueue: () => void }) {
  const { gpu, downloads, waiting, paused } = status.workers
  const step = gpu.step ? (STEP_NAME[gpu.step] ?? gpu.step) : null
  return (
    <Card className="gap-3 shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HugeiconsIcon icon={Task01Icon} className="size-4 text-muted-foreground" />
          处理
        </CardTitle>
        <CardDescription>一次只有一个任务用 GPU；下载可以同时进行</CardDescription>
        <CardAction>
          <Button variant="ghost" size="sm" onClick={onShowQueue}>
            查看队列
            <HugeiconsIcon icon={ArrowRight01Icon} data-icon="inline-end" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground">GPU</dt>
          <dd className="min-w-0">
            {gpu.busy && gpu.task_id ? (
              <button
                type="button"
                onClick={() => navigate(`#/result/task/${gpu.task_id}`)}
                className="flex min-w-0 max-w-full items-baseline gap-1.5 text-left hover:text-primary"
              >
                <span className="truncate">{gpu.title ?? "处理中"}</span>
                {step && <span className="shrink-0 text-xs text-muted-foreground">{step}</span>}
              </button>
            ) : (
              <span className="text-muted-foreground">空闲</span>
            )}
          </dd>
          <dt className="text-muted-foreground">下载</dt>
          <dd className="tabular-nums">{downloads.active} / {downloads.slots} 个在用</dd>
          <dt className="text-muted-foreground">等待</dt>
          <dd className="tabular-nums">
            {waiting} 个
            {paused > 0 && <span className="text-muted-foreground"> · 暂停 {paused} 个</span>}
          </dd>
        </dl>
      </CardContent>
    </Card>
  )
}

function DiskCard({ status }: { status: SystemStatus }) {
  const { disk } = status
  const usedPercent = percent(disk.used, disk.total)
  const low = disk.free !== null && disk.total !== null && (disk.free < LOW_DISK_BYTES || disk.free / disk.total < 0.05)
  return (
    <Card className="gap-3 shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HugeiconsIcon icon={HardDriveIcon} className="size-4 text-muted-foreground" />
          资料库磁盘
        </CardTitle>
        <CardDescription className="truncate font-mono text-xs" title={disk.path}>{disk.path}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1.5">
        {disk.free !== null && disk.total !== null ? (
          <>
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-muted-foreground">剩余</span>
              <span className="tabular-nums">
                {gigabytes(disk.free)}
                <span className="ml-1.5 text-muted-foreground">共 {gigabytes(disk.total)}</span>
              </span>
            </div>
            <Meter value={usedPercent} warn={low} />
            {low && <p className="text-xs text-amber-700 dark:text-amber-400">空间不足，下载和处理长视频可能失败。</p>}
          </>
        ) : (
          <p className="text-sm text-destructive">读不到磁盘信息：{disk.error ?? "未知错误"}</p>
        )}
      </CardContent>
    </Card>
  )
}

function ErrorsCard({ status }: { status: SystemStatus }) {
  const { errors, failed_tasks_24h: failed } = status
  return (
    <Card className="gap-3 shadow-none xl:col-span-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HugeiconsIcon icon={Alert02Icon} className="size-4 text-muted-foreground" />
          最近 24 小时
        </CardTitle>
        <CardDescription>
          {errors.count ? `${errors.count} 条错误日志` : "没有错误日志"}
          {failed > 0 ? ` · ${failed} 个任务失败` : " · 没有任务失败"}
        </CardDescription>
        <CardAction className="flex gap-1">
          {failed > 0 && (
            <Button variant="ghost" size="sm" onClick={() => navigate(buildHash("files", { status: "failed" }))}>
              查看失败的任务
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => navigate(buildHash("backend", { tab: "logs" }), { replace: true })}>
            打开日志
            <HugeiconsIcon icon={ArrowRight01Icon} data-icon="inline-end" />
          </Button>
        </CardAction>
      </CardHeader>
      {errors.recent.length > 0 && (
        <CardContent className="px-2">
          <ul className="flex flex-col">
            {errors.recent.map((error) => (
              <li key={`${error.time}-${error.module}-${error.message}`}>
                <button
                  type="button"
                  onClick={() => navigate(buildHash("backend", { tab: "logs", q: logQuery(error) }), { replace: true })}
                  className="flex w-full min-w-0 items-baseline gap-3 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                  title="在日志里查看"
                >
                  <time className="w-16 shrink-0 text-xs tabular-nums text-muted-foreground">{errorTime(error.time)}</time>
                  <span className="w-48 shrink-0 truncate font-mono text-xs text-muted-foreground" title={error.module}>
                    {error.event && error.event !== "log" ? error.event : error.module}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{error.message}</span>
                  {error.count > 1 && <Badge variant="secondary" className="shrink-0 tabular-nums">×{error.count}</Badge>}
                </button>
              </li>
            ))}
          </ul>
        </CardContent>
      )}
    </Card>
  )
}

/** GPU, workers, disk and recent errors: only what someone can act on. */
export function SystemPanel({ active, onShowQueue }: { active: boolean; onShowQueue: () => void }) {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!active) return
    let cancelled = false
    const load = async () => {
      try {
        const next = await api.system()
        if (cancelled) return
        setStatus(next)
        setError(null)
      } catch (requestError) {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : String(requestError))
      }
    }
    void load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load()
    }, 5_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [active])

  if (!status) {
    return error ? (
      <p className="rounded-lg border border-destructive/30 px-3 py-2 text-sm text-destructive" role="alert">读取系统状态失败：{error}</p>
    ) : (
      <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
        <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />
        正在读取系统状态…
      </div>
    )
  }

  const running = uptime(status.started_at)
  return (
    <div className="flex flex-col gap-4">
      {error && <p className="text-xs text-destructive" role="alert">刷新失败：{error}</p>}
      <div className="grid gap-4 xl:grid-cols-3">
        <GpuCard status={status} />
        <WorkerCard status={status} onShowQueue={onShowQueue} />
        <DiskCard status={status} />
        <ErrorsCard status={status} />
      </div>
      <p className="text-xs text-muted-foreground">
        MPP {status.version}
        {running && ` · 已运行 ${running}`}
        {` · 进程 ${status.pid}`}
      </p>
    </div>
  )
}
