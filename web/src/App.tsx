import { useEffect, useState } from "react"
import { useRoute, navigate } from "@/lib/router"
import { getPreferences } from "@/hooks/use-preferences"
import { FilesPage, type ArchiveSort } from "@/components/pages/files-page"
import { SubmitPage } from "@/components/pages/submit-page"
import { BackendPage } from "@/components/pages/backend-page"
import { ResultPageWrapper } from "@/components/pages/result-page-wrapper"
import { SettingsPage } from "@/components/pages/settings-page"
import { TaskQueueDropdown } from "@/components/task-queue-dropdown"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/select"
import { PlatformIcon } from "@/components/platform-icon"
import {
  MEDIA_FILTER_OPTIONS,
  SOURCE_FILTER_OPTIONS,
  type MediaFilter,
  type SourceFilter,
  type SourceFilterOption,
} from "@/lib/archive-filters"
import { HugeiconsIcon } from "@hugeicons/react"
import { ComputerTerminal01Icon, FolderOpenIcon, PlusSignIcon, Settings01Icon, Search01Icon } from "@hugeicons/core-free-icons"
import { cn } from "@/lib/utils"

const SOURCE_ICON_PLATFORMS = new Set([
  "apple_podcast",
  "bilibili",
  "webpage",
  "x",
  "xiaohongshu",
  "youtube",
  "zhihu",
])

function SourceFilterIcon({ option, className }: { option: SourceFilterOption; className?: string }) {
  if (option.platform && SOURCE_ICON_PLATFORMS.has(option.platform)) {
    return <PlatformIcon platform={option.platform} className={className ?? "size-4 shrink-0"} iconOnly />
  }
  return <HugeiconsIcon icon={FolderOpenIcon} className={className ?? "size-4 shrink-0 text-muted-foreground"} />
}

export default function App() {
  const route = useRoute()
  const [search, setSearch] = useState("")
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all")
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all")
  const [archiveSort, setArchiveSort] = useState<ArchiveSort>("created_desc")
  const showLibraryTools = route.page === "files"
  const selectedMediaFilter = MEDIA_FILTER_OPTIONS.find((option) => option.value === mediaFilter) ?? MEDIA_FILTER_OPTIONS[0]
  const selectedSourceFilter = SOURCE_FILTER_OPTIONS.find((option) => option.value === sourceFilter) ?? SOURCE_FILTER_OPTIONS[0]

  // Startup page routing
  useEffect(() => {
    if (window.location.hash && window.location.hash !== "#/" && window.location.hash !== "#") return
    const prefs = getPreferences()
    if (prefs.startupPage === "last" && prefs.lastArchivePath) {
      navigate(`#/result/archive?path=${encodeURIComponent(prefs.lastArchivePath)}`, { replace: true })
    } else {
      navigate("#/files", { replace: true })
    }
  }, [])

  useEffect(() => {
    const handleContextMenu = (event: MouseEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest("[data-slot='context-menu-trigger']")) return
      event.preventDefault()
    }

    window.addEventListener("contextmenu", handleContextMenu, true)
    return () => window.removeEventListener("contextmenu", handleContextMenu, true)
  }, [])

  const navItems = [
    { page: "files" as const, icon: FolderOpenIcon, label: "文件" },
    { page: "submit" as const, icon: PlusSignIcon, label: "处理" },
    { page: "backend" as const, icon: ComputerTerminal01Icon, label: "后端" },
  ]

  return (
    <div className="flex h-screen supports-[height:100dvh]:h-dvh flex-col bg-background">
      {/* Header */}
      <header className="shrink-0 border-b bg-card">
        <div className="flex min-h-12 flex-wrap items-center gap-2 px-3 py-2 sm:px-4">
          <div className="flex shrink-0 items-center gap-2">
            <img src="/favicon.svg" className="h-5 w-5" alt="" aria-hidden="true" />
            <span className="text-sm font-semibold">MPP</span>
          </div>

          {/* Nav links */}
          <nav className="flex shrink-0 items-center gap-0.5">
            {navItems.map((item) => (
              <button
                key={item.page}
                onClick={() => navigate(`#/${item.page}`)}
                aria-label={item.label}
                title={item.label}
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors sm:px-3",
                  route.page === item.page
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted",
                )}
              >
                <HugeiconsIcon icon={item.icon} className="h-4 w-4" />
                <span className="hidden sm:inline">{item.label}</span>
              </button>
            ))}
          </nav>

          {showLibraryTools ? (
            <div className="order-last flex w-full min-w-0 flex-wrap items-center gap-2 lg:order-none lg:ml-4 lg:w-auto lg:flex-1 lg:flex-nowrap">
              <div className="relative min-w-0 basis-full sm:basis-auto sm:flex-1 lg:min-w-[140px] lg:max-w-xs">
                <HugeiconsIcon icon={Search01Icon} className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜索标题、话题、关键词..."
                  className="pl-8 h-8 text-sm"
                  autoComplete="off"
                />
              </div>
              <Select value={mediaFilter} onValueChange={(value) => setMediaFilter(value as MediaFilter)}>
                <SelectTrigger size="sm" className="h-8 w-[76px] shrink-0 sm:w-[92px]">
                  <span className="truncate">{selectedMediaFilter.label}</span>
                </SelectTrigger>
                <SelectContent position="popper" align="end">
                  <SelectGroup>
                    {MEDIA_FILTER_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <Select value={sourceFilter} onValueChange={(value) => setSourceFilter(value as SourceFilter)}>
                <SelectTrigger size="sm" className="h-8 w-[120px] shrink-0 sm:w-[142px]">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <SourceFilterIcon option={selectedSourceFilter} />
                    <span className="truncate">{selectedSourceFilter.label}</span>
                  </span>
                </SelectTrigger>
                <SelectContent position="popper" align="end">
                  <SelectGroup>
                    {SOURCE_FILTER_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        <span className="flex items-center gap-2">
                          <SourceFilterIcon option={option} />
                          <span>{option.label}</span>
                        </span>
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <Select value={archiveSort} onValueChange={(value) => setArchiveSort(value as ArchiveSort)}>
                <SelectTrigger size="sm" className="h-8 w-[116px] shrink-0 sm:w-[132px]">
                  <span className="truncate">
                    {archiveSort === "created_desc" ? "最新创建" : archiveSort === "created_asc" ? "最早创建" : archiveSort === "published_desc" ? "最新发布" : "标题排序"}
                  </span>
                </SelectTrigger>
                <SelectContent position="popper" align="end">
                  <SelectGroup>
                    <SelectItem value="created_desc">最新创建</SelectItem>
                    <SelectItem value="created_asc">最早创建</SelectItem>
                    <SelectItem value="published_desc">最新发布</SelectItem>
                    <SelectItem value="title_asc">标题排序</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div className="min-w-0 flex-1" />
          )}

          {/* Task queue dropdown */}
          <TaskQueueDropdown />

          {/* Settings nav button */}
          <button
            onClick={() => navigate("#/settings")}
            className={cn(
              "rounded-md p-2 transition-colors",
              route.page === "settings"
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            )}
            title="设置"
          >
            <HugeiconsIcon icon={Settings01Icon} className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 min-h-0">
        {route.page === "files" && <FilesPage search={search} mediaFilter={mediaFilter} sourceFilter={sourceFilter} sort={archiveSort} />}
        {route.page === "submit" && <SubmitPage />}
        {route.page === "backend" && <BackendPage />}
        {route.page === "result" && <ResultPageWrapper />}
        {route.page === "settings" && <SettingsPage />}
      </main>
    </div>
  )
}
