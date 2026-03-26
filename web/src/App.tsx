import { useEffect, useState } from "react"
import { useRoute, navigate } from "@/lib/router"
import { getPreferences } from "@/hooks/use-preferences"
import { FilesPage } from "@/components/pages/files-page"
import { SubmitPage } from "@/components/pages/submit-page"
import { ResultPageWrapper } from "@/components/pages/result-page-wrapper"
import { SettingsPage } from "@/components/pages/settings-page"
import { TaskQueueDropdown } from "@/components/task-queue-dropdown"
import { Input } from "@/components/ui/input"
import { AudioLines, FolderOpen, Plus, Settings, Search } from "lucide-react"
import { cn } from "@/lib/utils"

export default function App() {
  const route = useRoute()
  const [search, setSearch] = useState("")
  const [mediaFilter, setMediaFilter] = useState<"all" | "video" | "audio">("all")

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

  const navItems = [
    { page: "files" as const, icon: FolderOpen, label: "文件" },
    { page: "submit" as const, icon: Plus, label: "处理" },
  ]

  return (
    <div className="flex h-screen flex-col bg-background">
      {/* Header */}
      <header className="shrink-0 border-b bg-card">
        <div className="flex items-center h-12 px-4 gap-1">
          <AudioLines className="h-4.5 w-4.5 text-primary mr-2" aria-hidden="true" />
          <span className="text-sm font-semibold mr-4">MPP</span>

          {/* Nav links */}
          <nav className="flex items-center gap-0.5">
            {navItems.map((item) => (
              <button
                key={item.page}
                onClick={() => navigate(`#/${item.page}`)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                  route.page === item.page
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted",
                )}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </button>
            ))}
          </nav>

          {/* Search + filter — always visible */}
          <div className="flex items-center gap-2 ml-6 flex-1">
            <div className="relative max-w-xs flex-1">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索标题、话题、关键词..."
                className="pl-8 h-8 text-sm"
                autoComplete="off"
                onFocus={() => { if (route.page !== "files") navigate("#/files") }}
              />
            </div>
            <div className="flex rounded-md border text-xs">
              {(["all", "video", "audio"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => { setMediaFilter(f); if (route.page !== "files") navigate("#/files") }}
                  className={cn(
                    "px-2.5 py-1 transition-colors",
                    mediaFilter === f
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                    f === "all" && "rounded-l-md",
                    f === "audio" && "rounded-r-md",
                  )}
                >
                  {f === "all" ? "全部" : f === "video" ? "视频" : "音频"}
                </button>
              ))}
            </div>
          </div>

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
            <Settings className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 min-h-0">
        {route.page === "files" && <FilesPage search={search} mediaFilter={mediaFilter} />}
        {route.page === "submit" && <SubmitPage />}
        {route.page === "result" && <ResultPageWrapper />}
        {route.page === "settings" && <SettingsPage />}
      </main>
    </div>
  )
}
