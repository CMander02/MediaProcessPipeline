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
  ComputerTerminal01Icon,
  Copy01Icon,
  Download01Icon,
  Loading03Icon,
  PauseIcon,
  PlayIcon,
  Search01Icon,
} from "@hugeicons/core-free-icons"

import { ActivityContent } from "@/components/activity/activity-content"
import { SystemPanel } from "@/components/backend/system-panel"
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
import { useActiveTasks } from "@/hooks/use-active-tasks"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { api, type BackendLogEntry, type BackendLogFile } from "@/lib/api"
import { buildHash, navigate, useRoute } from "@/lib/router"
import { cn } from "@/lib/utils"

type BackendTab = "system" | "tasks" | "logs"

const backendTabs: Array<{ value: BackendTab; label: string }> = [
  { value: "system", label: "系统" },
  { value: "tasks", label: "任务队列" },
  { value: "logs", label: "实时日志" },
]

// Older links point at tabs that are now part of 系统.
function backendTabFromParam(requested: string | undefined): BackendTab {
  return backendTabs.some((tab) => tab.value === requested) ? requested as BackendTab : "system"
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatLogTime(value: string) {
  return value.length >= 23 ? value.slice(5, 23) : value || "--"
}

function TaskQueuePanel() {
  const { tasks } = useActiveTasks()
  const count = tasks.length
  return (
    <Card className="gap-0 py-0 shadow-none">
      <CardHeader className="border-b py-4">
        <CardTitle>任务队列</CardTitle>
        <CardDescription>与标题栏「活动」面板相同：进行中、排队、暂停和最近失败的任务</CardDescription>
        <CardAction><Badge variant="secondary">{count}</Badge></CardAction>
      </CardHeader>
      <CardContent className="py-2">
        <ActivityContent />
      </CardContent>
    </Card>
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

function LogPanel({ active, online, initialSearch = "" }: { active: boolean; online: boolean; initialSearch?: string }) {
  const [files, setFiles] = useState<BackendLogFile[]>([])
  const [selectedFile, setSelectedFile] = useState("")
  const [entries, setEntries] = useState<BackendLogEntry[]>([])
  const [selectedEntry, setSelectedEntry] = useState<BackendLogEntry | null>(null)
  const [live, setLive] = useState(true)
  const [autoScroll, setAutoScroll] = useState(true)
  const [level, setLevel] = useState("ALL")
  const [moduleName, setModuleName] = useState("ALL")
  const [search, setSearch] = useState(initialSearch)
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
  const route = useRoute()
  const activeTab = backendTabFromParam(route.params.tab)
  const showTab = (value: string) => navigate(buildHash("backend", { tab: value }), { replace: true })

  if (!online) {
    return <OfflineState className="h-full" title="后端当前离线" description="连接服务器后可查看运行状态、日志和任务控制。" />
  }

  return (
    <section className="h-full min-h-0 overflow-y-auto bg-background">
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-4 p-3 pb-8 sm:p-6">
        <header className="min-w-0">
          <div className="flex items-center gap-2 text-lg font-semibold md:text-xl">
            <HugeiconsIcon icon={ComputerTerminal01Icon} className="size-5 text-primary" />
            后端
          </div>
          <p className="mt-1 text-sm text-muted-foreground">GPU、磁盘、任务队列和日志。</p>
        </header>

        <Tabs value={activeTab} onValueChange={showTab} className="gap-0">
          <div className="grid min-w-0 gap-4 lg:grid-cols-[11rem_minmax(0,1fr)] lg:items-start">
            <aside className="min-w-0 lg:sticky lg:top-4">
              <div className="overflow-x-auto rounded-lg border p-1">
                <TabsList className="flex w-full justify-start gap-1 bg-transparent p-0 group-data-horizontal/tabs:h-auto lg:flex-col">
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
              <TabsContent value="system">
                <SystemPanel active={activeTab === "system"} onShowQueue={() => showTab("tasks")} />
              </TabsContent>
              <TabsContent value="tasks"><TaskQueuePanel /></TabsContent>
              <TabsContent value="logs">
                <LogPanel key={route.params.q ?? ""} active={activeTab === "logs"} online={online} initialSearch={route.params.q ?? ""} />
              </TabsContent>
            </div>
          </div>
        </Tabs>
      </div>
    </section>
  )
}
