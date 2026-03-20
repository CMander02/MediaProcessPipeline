import { useState, useMemo } from "react"
import { useArchives } from "@/hooks/use-archives"
import { usePreferences } from "@/hooks/use-preferences"
import { navigate } from "@/lib/router"
import { ArchiveCard } from "@/components/archive-card"
import { Input } from "@/components/ui/input"
import { Loader2, Search, FolderOpen } from "lucide-react"

export function FilesPage() {
  const { archives, loading } = useArchives()
  const { update: updatePrefs } = usePreferences()
  const [search, setSearch] = useState("")
  const [mediaFilter, setMediaFilter] = useState<"all" | "video" | "audio">("all")

  const filtered = useMemo(() => {
    let list = archives
    if (mediaFilter === "video") list = list.filter((a) => a.has_video)
    if (mediaFilter === "audio") list = list.filter((a) => !a.has_video && a.has_audio)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(
        (a) =>
          a.title.toLowerCase().includes(q) ||
          a.analysis?.content_type?.toLowerCase().includes(q) ||
          a.analysis?.main_topics?.some((t) => t.toLowerCase().includes(q)) ||
          a.analysis?.keywords?.some((k) => k.toLowerCase().includes(q)),
      )
    }
    return list
  }, [archives, search, mediaFilter])

  const handleOpen = (path: string) => {
    updatePrefs({ lastArchivePath: path })
    navigate(`#/result/archive?path=${encodeURIComponent(path)}`)
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索标题、话题、关键词..."
            className="pl-9 h-9"
            autoComplete="off"
          />
        </div>
        <div className="flex rounded-md border text-sm">
          {(["all", "video", "audio"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setMediaFilter(f)}
              className={`px-3 py-1.5 text-xs transition-colors ${
                mediaFilter === f
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              } ${f === "all" ? "rounded-l-md" : f === "audio" ? "rounded-r-md" : ""}`}
            >
              {f === "all" ? "全部" : f === "video" ? "视频" : "音频"}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      {filtered.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4 overflow-y-auto flex-1">
          {filtered.map((a) => (
            <ArchiveCard key={a.path} archive={a} onClick={() => handleOpen(a.path)} />
          ))}
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
          <FolderOpen className="h-12 w-12 opacity-20" />
          {archives.length === 0 ? (
            <p>还没有归档结果。处理完成后这里会显示文件。</p>
          ) : (
            <p>没有匹配的结果</p>
          )}
        </div>
      )}
    </div>
  )
}
