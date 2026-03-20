import { useEffect, useState } from "react"
import { useRoute, navigate } from "@/lib/router"
import { getPreferences } from "@/hooks/use-preferences"
import { FilesPage } from "@/components/pages/files-page"
import { SubmitPage } from "@/components/pages/submit-page"
import { ResultPageWrapper } from "@/components/pages/result-page-wrapper"
import { SettingsModal } from "@/components/settings-modal"
import { AudioLines, FolderOpen, Plus, Settings } from "lucide-react"
import { cn } from "@/lib/utils"

export default function App() {
  const route = useRoute()
  const [settingsOpen, setSettingsOpen] = useState(false)

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

          <div className="flex-1" />

          {/* Settings gear */}
          <button
            onClick={() => setSettingsOpen(true)}
            className="rounded-md p-2 text-muted-foreground transition-colors hover:text-foreground hover:bg-muted"
            title="设置"
          >
            <Settings className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 min-h-0">
        {route.page === "files" && <FilesPage />}
        {route.page === "submit" && <SubmitPage />}
        {route.page === "result" && <ResultPageWrapper />}
      </main>

      {/* Settings modal */}
      <SettingsModal open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  )
}
