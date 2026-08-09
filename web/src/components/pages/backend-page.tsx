import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Activity01Icon,
  AiBrain01Icon,
  Alert02Icon,
  CheckmarkCircle02Icon,
  Clock01Icon,
  ComputerTerminal01Icon,
  Copy01Icon,
  Database01Icon,
  Download01Icon,
  File01Icon,
  InformationCircleIcon,
  Loading03Icon,
  PauseIcon,
  PlayIcon,
  RefreshIcon,
  Search01Icon,
  ServerStack01Icon,
  Task01Icon,
} from "@hugeicons/core-free-icons"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { EmptyState, OfflineState } from "@/components/ui/page-state"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useAppAccess } from "@/hooks/use-app-access-context"
import {
  api,
  type BackendLogEntry,
  type BackendLogFile,
  type HealthInfo,
  type Settings,
  type Task,
  type TaskStats,
} from "@/lib/api"
import { navigate } from "@/lib/router"
import { cn } from "@/lib/utils"

type BackendTab = "overview" | "logs" | "tasks" | "models" | "diagnostics"
type IconData = Parameters<typeof HugeiconsIcon>[0]["icon"]

const activeStatuses = new Set<Task["status"]>(["queued", "processing", "paused"])
const backendTabs: Array<{ value: BackendTab; label: string }> = [
  { value: "overview", label: "概览" },
  { value: "logs", label: "实时日志" },
  { value: "tasks", label: "任务队列" },
  { value: "models", label: "模型运行" },
  { value: "diagnostics", label: "诊断" },
]

function initialBackendTab(): BackendTab {
  const requested = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("tab")
  return backendTabs.some((tab) => tab.value === requested) ? requested as BackendTab : "overview"
}

function statusLabel(status: Task["status"]) {
  if (status === "processing") return "处理中"
  if (status === "queued") return "排队中"
  if (status === "paused") return "已暂停"
  return status
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatLogTime(value: string) {
  return value.length >= 23 ? value.slice(5, 23) : value || "--"
}

function InfoRow({
  icon,
  label,
  value,
  detail,
}: {
  icon: IconData
  label: string
  value: string
  detail?: string
}) {
  return (
    <div className="flex min-h-14 items-center gap-3 py-2.5">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <HugeiconsIcon icon={icon} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs text-muted-foreground">{label}</span>
        <span className="mt-0.5 block truncate text-sm font-medium" title={value}>{value}</span>
      </span>
      {detail ? <span className="hidden shrink-0 text-xs text-muted-foreground sm:block">{detail}</span> : null}
    </div>
  )
}

function StatusStrip({
  serviceHealthy,
  activeCount,
  modelState,
  version,
}: {
  serviceHealthy: boolean
  activeCount: number
  modelState: string
  version: string
}) {
  const items = [
    { icon: ServerStack01Icon, label: serviceHealthy ? "服务正常" : "检查中" },
    { icon: Task01Icon, label: `${activeCount} 个活动任务` },
    { icon: AiBrain01Icon, label: `模型${modelState}` },
    { icon: InformationCircleIcon, label: version },
  ]

  return (
    <Card size="sm" className="shadow-none">
      <CardContent className="grid grid-cols-2 p-0 xl:grid-cols-4">
        {items.map((item, index) => (
          <div
            key={item.label}
            className={cn(
              "flex min-h-12 items-center gap-2 px-4",
              index % 2 === 0 ? "border-r" : "",
              index < 2 ? "border-b xl:border-b-0" : "",
              index > 0 ? "xl:border-l" : "",
              index % 2 === 0 ? "xl:border-r-0" : "",
            )}
          >
            <HugeiconsIcon icon={item.icon} className="size-4 text-muted-foreground" />
            <span className="truncate text-sm font-medium">{item.label}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function RuntimeCard({
  title,
  description,
  icon,
  rows,
}: {
  title: string
  description: string
  icon: IconData
  rows: Array<{ icon: IconData; label: string; value: string; detail?: string }>
}) {
  return (
    <Card className="gap-0 py-0 shadow-none">
      <CardHeader className="border-b py-4">
        <span className="flex items-center gap-2">
          <span className="flex size-9 items-center justify-center rounded-lg bg-muted text-primary">
            <HugeiconsIcon icon={icon} />
          </span>
          <span>
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </span>
        </span>
      </CardHeader>
      <CardContent>
        {rows.map((row, index) => (
          <div key={row.label}>
            {index > 0 ? <Separator /> : null}
            <InfoRow {...row} />
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function OverviewPanel({
  serviceHealthy,
  activeCount,
  processingCount,
  health,
  settings,
  lastUpdateText,
}: {
  serviceHealthy: boolean
  activeCount: number
  processingCount: number
  health: HealthInfo | null
  settings: Settings | null
  lastUpdateText: string
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <RuntimeCard
        title="服务信息"
        description="当前后端实例与任务运行状态"
        icon={ServerStack01Icon}
        rows={[
          { icon: CheckmarkCircle02Icon, label: "服务状态", value: serviceHealthy ? "运行正常" : "等待检查" },
          { icon: Task01Icon, label: "活动任务", value: `${activeCount} 个`, detail: `${processingCount} 个正在处理` },
          { icon: InformationCircleIcon, label: "服务版本", value: health?.version ?? "--" },
          { icon: Clock01Icon, label: "最后检查", value: lastUpdateText },
        ]}
      />
      <RuntimeCard
        title="处理配置"
        description="当前生效的识别与分析模型"
        icon={AiBrain01Icon}
        rows={[
          { icon: Activity01Icon, label: "ASR", value: settings?.asr_provider || "未配置" },
          { icon: AiBrain01Icon, label: "LLM", value: settings?.llm_provider || "未配置" },
          { icon: ComputerTerminal01Icon, label: "工作器", value: processingCount > 0 ? "处理中" : "空闲" },
          { icon: Database01Icon, label: "任务存储", value: "SQLite", detail: "活动与历史统一存储" },
        ]}
      />
    </div>
  )
}

function TaskQueuePanel({ tasks }: { tasks: Task[] }) {
  return (
    <Card className="gap-0 py-0 shadow-none">
      <CardHeader className="border-b py-4">
        <CardTitle>任务队列</CardTitle>
        <CardDescription>排队、处理和暂停中的任务</CardDescription>
        <CardAction><Badge variant="secondary">{tasks.length}</Badge></CardAction>
      </CardHeader>
      <CardContent>
        {tasks.length === 0 ? (
          <EmptyState title="当前队列为空" description="新任务提交后会显示在这里。" className="min-h-[320px]" />
        ) : (
          tasks.map((task, index) => (
            <div key={task.id}>
              {index > 0 ? <Separator /> : null}
              <button
                type="button"
                onClick={() => navigate(`#/result/task/${task.id}`)}
                className="flex min-h-16 w-full items-center gap-3 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className={cn("size-2 shrink-0 rounded-full", task.status === "processing" ? "bg-primary" : "bg-muted-foreground/50")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{task.source.split(/[/\\]/).pop() || task.id}</span>
                  <span className="mt-1 block truncate text-xs text-muted-foreground">{task.message || task.current_step || "等待处理"}</span>
                </span>
                <span className="shrink-0 text-right">
                  <Badge variant="outline">{statusLabel(task.status)}</Badge>
                  <span className="mt-1 block text-xs tabular-nums text-muted-foreground">{Math.round((task.progress ?? 0) * 100)}%</span>
                </span>
              </button>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}

function ModelPanel({ settings, processingCount }: { settings: Settings | null; processingCount: number }) {
  return (
    <RuntimeCard
      title="模型运行"
      description="当前模型选择与单 GPU 工作器状态"
      icon={AiBrain01Icon}
      rows={[
        { icon: Activity01Icon, label: "语音识别提供方", value: settings?.asr_provider || "未配置" },
        { icon: File01Icon, label: "音频处理流程", value: settings?.audio_processing_flow || "未配置" },
        { icon: AiBrain01Icon, label: "文本分析提供方", value: settings?.llm_provider || "未配置" },
        { icon: ComputerTerminal01Icon, label: "GPU 工作器", value: processingCount > 0 ? "占用中" : "空闲", detail: "单工作器串行处理" },
      ]}
    />
  )
}

function DiagnosticsPanel({
  serviceHealthy,
  settings,
  processingCount,
}: {
  serviceHealthy: boolean
  settings: Settings | null
  processingCount: number
}) {
  return (
    <RuntimeCard
      title="运行诊断"
      description="后端核心链路的当前可用状态"
      icon={Activity01Icon}
      rows={[
        { icon: serviceHealthy ? CheckmarkCircle02Icon : Alert02Icon, label: "HTTP API", value: serviceHealthy ? "连接正常" : "等待响应", detail: "localhost:18000" },
        { icon: File01Icon, label: "文件日志", value: "已启用", detail: "支持实时读取与历史文件切换" },
        { icon: Database01Icon, label: "运行配置", value: settings ? "已加载" : "等待加载" },
        { icon: ComputerTerminal01Icon, label: "任务工作器", value: processingCount > 0 ? "正在处理任务" : "等待任务" },
      ]}
    />
  )
}

function LogLevelBadge({ level }: { level: string }) {
  if (level === "ERROR" || level === "CRIT") return <Badge variant="destructive">{level}</Badge>
  if (level === "WARN") return <Badge variant="secondary">{level}</Badge>
  if (level === "DEBUG" || level === "RAW") return <Badge variant="ghost">{level}</Badge>
  return <Badge variant="outline">{level}</Badge>
}

function LogDetailsSheet({ entry, onOpenChange }: { entry: BackendLogEntry | null; onOpenChange: (open: boolean) => void }) {
  const copyRaw = useCallback(() => {
    if (entry) void navigator.clipboard.writeText(entry.raw)
  }, [entry])

  return (
    <Sheet open={entry !== null} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[min(92vw,540px)] sm:max-w-[540px]">
        <SheetHeader className="border-b">
          <SheetTitle>日志详情</SheetTitle>
          <SheetDescription>{entry?.timestamp || "选择一条日志查看完整内容"}</SheetDescription>
        </SheetHeader>
        {entry ? (
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <dl className="flex flex-col gap-3 text-sm">
              <div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">级别</dt><dd><LogLevelBadge level={entry.level} /></dd></div>
              <Separator />
              <div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">模块</dt><dd className="truncate font-mono">{entry.module || "-"}</dd></div>
              <Separator />
              <div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">事件</dt><dd className="truncate font-mono">{entry.event || "-"}</dd></div>
              <Separator />
              <div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">任务 ID</dt><dd className="truncate font-mono">{entry.task_id || "-"}</dd></div>
              <Separator />
              <div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">工作器</dt><dd className="truncate font-mono">{entry.worker || "-"}</dd></div>
              {entry.source ? <><Separator /><div className="flex items-center justify-between gap-4"><dt className="text-muted-foreground">来源</dt><dd className="truncate font-mono">{entry.source}</dd></div></> : null}
            </dl>
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">完整内容</h3>
              <pre className="min-h-44 whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-mono text-xs leading-5">{entry.raw}</pre>
            </div>
          </div>
        ) : null}
        <SheetFooter className="border-t">
          <Button variant="outline" onClick={copyRaw} disabled={!entry}>
            <HugeiconsIcon icon={Copy01Icon} data-icon="inline-start" />
            复制日志
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

function LogPanel({ active, online }: { active: boolean; online: boolean }) {
  const [files, setFiles] = useState<BackendLogFile[]>([])
  const [selectedFile, setSelectedFile] = useState("")
  const [entries, setEntries] = useState<BackendLogEntry[]>([])
  const [selectedEntry, setSelectedEntry] = useState<BackendLogEntry | null>(null)
  const [live, setLive] = useState(true)
  const [autoScroll, setAutoScroll] = useState(true)
  const [level, setLevel] = useState("ALL")
  const [moduleName, setModuleName] = useState("ALL")
  const [search, setSearch] = useState("")
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cursorRef = useRef(0)
  const selectedFileRef = useRef("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const deferredSearch = useDeferredValue(search.trim().toLocaleLowerCase())

  const loadFile = useCallback(async (file: string) => {
    if (!file) return
    setLoading(true)
    try {
      const response = await api.logs.read({ file })
      selectedFileRef.current = file
      cursorRef.current = response.cursor
      setSelectedFile(file)
      setEntries(response.entries)
      setConnected(true)
      setError(null)
    } catch (requestError) {
      setConnected(false)
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!active || !online) return
    let cancelled = false
    const initialize = async () => {
      setLoading(true)
      try {
        const fileResponse = await api.logs.files()
        if (cancelled) return
        setFiles(fileResponse.files)
        const preferred = selectedFileRef.current && fileResponse.files.some((file) => file.name === selectedFileRef.current)
          ? selectedFileRef.current
          : fileResponse.active_file || fileResponse.files[0]?.name || ""
        if (preferred) await loadFile(preferred)
      } catch (requestError) {
        if (!cancelled) {
          setConnected(false)
          setError(requestError instanceof Error ? requestError.message : String(requestError))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void initialize()
    return () => { cancelled = true }
  }, [active, online, loadFile])

  useEffect(() => {
    if (!active || !online || !live || !selectedFile) return
    let cancelled = false
    const poll = async () => {
      try {
        const response = await api.logs.read({ file: selectedFile, cursor: cursorRef.current })
        if (cancelled) return
        cursorRef.current = response.cursor
        if (response.reset) {
          setEntries(response.entries)
        } else if (response.entries.length > 0) {
          setEntries((current) => [...current, ...response.entries])
        }
        setConnected(true)
        setError(null)
      } catch (requestError) {
        if (!cancelled) {
          setConnected(false)
          setError(requestError instanceof Error ? requestError.message : String(requestError))
        }
      }
    }
    const timer = window.setInterval(() => void poll(), 1_500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [active, live, online, selectedFile])

  const modules = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.module).filter(Boolean))).sort(),
    [entries],
  )
  const filteredEntries = useMemo(() => entries.filter((entry) => {
    if (level !== "ALL" && entry.level !== level) return false
    if (moduleName !== "ALL" && entry.module !== moduleName) return false
    if (!deferredSearch) return true
    return `${entry.timestamp} ${entry.level} ${entry.module} ${entry.task_id} ${entry.raw}`.toLocaleLowerCase().includes(deferredSearch)
  }), [deferredSearch, entries, level, moduleName])

  useEffect(() => {
    if (autoScroll && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [autoScroll, filteredEntries.length])

  const exportLogs = useCallback(() => {
    const text = filteredEntries.map((entry) => entry.raw).join("\n")
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = `${selectedFile || "mpp"}.filtered.log`
    anchor.click()
    URL.revokeObjectURL(url)
  }, [filteredEntries, selectedFile])

  return (
    <>
      <Card className="gap-0 py-0 shadow-none">
        <CardHeader className="border-b py-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex min-h-8 items-center gap-2 text-sm font-medium">
              <Switch checked={live} onCheckedChange={setLive} aria-label="实时跟随" />
              实时跟随
            </label>
            <Select value={selectedFile} onValueChange={(value) => void loadFile(value)}>
              <SelectTrigger className="w-full sm:w-56" aria-label="日志文件">
                <SelectValue placeholder="选择日志文件" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {files.map((file) => (
                    <SelectItem key={file.name} value={file.name}>
                      {file.active ? "当前 · " : ""}{file.name} · {formatBytes(file.size)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <Select value={level} onValueChange={setLevel}>
              <SelectTrigger className="w-32" aria-label="日志级别"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {["ALL", "DEBUG", "INFO", "WARN", "ERROR", "CRIT"].map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                </SelectGroup>
              </SelectContent>
            </Select>
            <Select value={moduleName} onValueChange={setModuleName}>
              <SelectTrigger className="w-40" aria-label="日志模块"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="ALL">全部模块</SelectItem>
                  {modules.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                </SelectGroup>
              </SelectContent>
            </Select>
            <div className="relative min-w-56 flex-1">
              <HugeiconsIcon icon={Search01Icon} className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="过滤日志内容、模块或任务 ID" className="pl-8" />
            </div>
            <label className="flex min-h-8 items-center gap-2 text-sm">
              <Switch checked={autoScroll} onCheckedChange={setAutoScroll} aria-label="自动滚动" />
              自动滚动
            </label>
            <Button variant="outline" onClick={exportLogs} disabled={filteredEntries.length === 0}>
              <HugeiconsIcon icon={Download01Icon} data-icon="inline-start" />
              导出
            </Button>
            <Button variant="ghost" onClick={() => setEntries([])} disabled={entries.length === 0}>清空视图</Button>
          </div>
        </CardHeader>
        {error ? <div className="border-b px-4 py-2 text-sm text-destructive" role="alert">日志读取失败：{error}</div> : null}
        <CardContent className="p-0">
          <div ref={scrollRef} className="h-[calc(100dvh-23rem)] min-h-[420px] overflow-y-auto">
            {loading && entries.length === 0 ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                <HugeiconsIcon icon={Loading03Icon} className="animate-spin" />
                正在读取完整日志…
              </div>
            ) : filteredEntries.length === 0 ? (
              <EmptyState title="当前没有匹配日志" description="调整级别、模块或搜索条件后继续查看。" className="h-full" />
            ) : (
              filteredEntries.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => setSelectedEntry(entry)}
                  className="block w-full border-b px-4 py-2.5 text-left transition-colors [content-visibility:auto] hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  <span className="flex flex-wrap items-center gap-2 text-xs">
                    <time className="tabular-nums text-muted-foreground">{formatLogTime(entry.timestamp)}</time>
                    <LogLevelBadge level={entry.level} />
                    <span className="font-mono text-muted-foreground">{entry.module || "raw"}</span>
                    {entry.task_id ? <span className="font-mono text-muted-foreground">任务 {entry.task_id}</span> : null}
                  </span>
                  <span className="mt-1 block whitespace-pre-wrap break-words font-mono text-xs leading-5 sm:text-sm">{entry.message}</span>
                </button>
              ))
            )}
          </div>
        </CardContent>
        <div className="grid min-h-11 grid-cols-3 items-center border-t px-4 text-xs text-muted-foreground">
          <span>{filteredEntries.length.toLocaleString("zh-CN")} / {entries.length.toLocaleString("zh-CN")} 条</span>
          <span className="flex items-center justify-center gap-2"><span className={cn("size-2 rounded-full", connected ? "bg-primary" : "bg-destructive")} />{connected ? "连接正常" : "连接中断"}</span>
          <Button variant="ghost" size="sm" className="justify-self-end" onClick={() => setLive((current) => !current)}>
            <HugeiconsIcon icon={live ? PauseIcon : PlayIcon} data-icon="inline-start" />
            {live ? "暂停" : "继续"}
          </Button>
        </div>
      </Card>
      <LogDetailsSheet entry={selectedEntry} onOpenChange={(open) => { if (!open) setSelectedEntry(null) }} />
    </>
  )
}

export function BackendPage() {
  const { online } = useAppAccess()
  const [activeTab, setActiveTab] = useState<BackendTab>(initialBackendTab)
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [stats, setStats] = useState<TaskStats | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const refresh = useCallback(async (showBusy = false) => {
    if (!online) return
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
  }, [online])

  useEffect(() => {
    if (!online) return
    void refresh()
    const timer = window.setInterval(() => void refresh(), 10_000)
    return () => window.clearInterval(timer)
  }, [online, refresh])

  const processingCount = stats?.processing ?? tasks.filter((task) => task.status === "processing").length
  const activeCount = stats
    ? (stats.processing ?? 0) + (stats.queued ?? 0) + (stats.paused ?? 0)
    : tasks.length
  const serviceHealthy = health?.status === "ok" || health?.status === "healthy"
  const modelState = processingCount > 0 ? "占用中" : "空闲"
  const lastUpdateText = useMemo(
    () => lastUpdated?.toLocaleTimeString("zh-CN", { hour12: false }) ?? "等待首次检查",
    [lastUpdated],
  )

  if (!online) {
    return <OfflineState className="h-full" title="后端当前离线" description="连接服务器后可查看运行状态、日志和任务控制。" />
  }

  return (
    <section className="h-full min-h-0 overflow-y-auto bg-background">
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-4 p-3 pb-8 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-lg font-semibold md:text-xl">
              <HugeiconsIcon icon={ComputerTerminal01Icon} className="size-5 text-primary" />
              后端运行中心
            </div>
            <p className="mt-1 text-sm text-muted-foreground">服务状态、完整日志、任务队列与模型运行统一查看。</p>
          </div>
          <Button className="h-11 md:h-9" variant="outline" onClick={() => void refresh(true)} disabled={refreshing}>
            <HugeiconsIcon icon={RefreshIcon} data-icon="inline-start" className={cn(refreshing && "animate-spin")} />
            刷新状态
          </Button>
        </header>

        {error ? <div className="rounded-lg border border-destructive/30 px-3 py-2 text-sm text-destructive" role="alert">服务状态读取失败：{error}</div> : null}

        <Tabs value={activeTab} onValueChange={(value) => {
          const nextTab = value as BackendTab
          setActiveTab(nextTab)
          window.history.replaceState(null, "", `#/backend?tab=${nextTab}`)
        }} className="gap-0">
          <div className="grid min-w-0 gap-4 lg:grid-cols-[11rem_minmax(0,1fr)] lg:items-start">
            <aside className="min-w-0 lg:sticky lg:top-4">
              <div className="overflow-x-auto rounded-lg border p-1">
                <TabsList className="flex min-w-[520px] w-full justify-start gap-1 bg-transparent p-0 group-data-horizontal/tabs:h-auto lg:min-w-0 lg:flex-col">
                  {backendTabs.map((tab) => (
                    <TabsTrigger
                      key={tab.value}
                      value={tab.value}
                      className="h-10 min-w-24 flex-none justify-center px-3 data-active:bg-muted data-active:text-primary data-active:shadow-none lg:w-full lg:justify-start"
                    >
                      {tab.label}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </div>
            </aside>

            <div className="flex min-w-0 flex-col gap-4">
              <StatusStrip serviceHealthy={serviceHealthy} activeCount={activeCount} modelState={modelState} version={health?.version ?? "0.5.0"} />

              <TabsContent value="overview">
                <OverviewPanel serviceHealthy={serviceHealthy} activeCount={activeCount} processingCount={processingCount} health={health} settings={settings} lastUpdateText={lastUpdateText} />
              </TabsContent>
              <TabsContent value="logs">
                <LogPanel active={activeTab === "logs"} online={online} />
              </TabsContent>
              <TabsContent value="tasks"><TaskQueuePanel tasks={tasks} /></TabsContent>
              <TabsContent value="models"><ModelPanel settings={settings} processingCount={processingCount} /></TabsContent>
              <TabsContent value="diagnostics"><DiagnosticsPanel serviceHealthy={serviceHealthy} settings={settings} processingCount={processingCount} /></TabsContent>
            </div>
          </div>
        </Tabs>
      </div>
    </section>
  )
}
