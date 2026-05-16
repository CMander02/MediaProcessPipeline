import { useState, useMemo, useEffect } from "react"
import { useArchives } from "@/hooks/use-archives"
import { usePreferences } from "@/hooks/use-preferences"
import { navigate } from "@/lib/router"
import { ArchiveCard } from "@/components/archive-card"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon, FolderOpenIcon, ArrowLeft01Icon, ArrowRight01Icon } from "@hugeicons/core-free-icons"

const PAGE_SIZE = 18

interface FilesPageProps {
  search: string
  mediaFilter: "all" | "video" | "audio"
}

export function FilesPage({ search, mediaFilter }: FilesPageProps) {
  const { archives, loading, refresh } = useArchives()
  const { update: updatePrefs } = usePreferences()
  const [page, setPage] = useState(1)
  const [deleteTarget, setDeleteTarget] = useState<{ title: string; path: string } | null>(null)

  const filtered = useMemo(() => {
    let list = archives
    if (mediaFilter === "video") list = list.filter((a) => a.has_video)
    if (mediaFilter === "audio") list = list.filter((a) => !a.has_video && a.has_audio)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter((a) => a.title.toLowerCase().includes(q))
    }
    // Surface in-progress tasks at the top so the user sees their just-submitted work first
    return [...list].sort((a, b) => {
      if (!!a.processing !== !!b.processing) return a.processing ? -1 : 1
      return 0
    })
  }, [archives, search, mediaFilter])

  // Reset page when filters change
  useEffect(() => { setPage(1) }, [search, mediaFilter])

  // While any task is processing, poll for updates so the queue card progress stays fresh
  const anyProcessing = archives.some((a) => a.processing)
  useEffect(() => {
    if (!anyProcessing) return
    const id = window.setInterval(() => { refresh(true) }, 3000)
    return () => window.clearInterval(id)
  }, [anyProcessing, refresh])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const paged = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  const handleOpen = (path: string, taskId?: string) => {
    updatePrefs({ lastArchivePath: path })
    const tid = taskId ? `&taskId=${encodeURIComponent(taskId)}` : ""
    navigate(`#/result/archive?path=${encodeURIComponent(path)}${tid}`)
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <HugeiconsIcon icon={Loading03Icon} className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 pb-2">
      {/* Grid */}
      {filtered.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4 overflow-y-auto flex-1 min-h-0 content-start">
          {paged.map((a) => (
            <ArchiveCard
              key={a.path}
              archive={a}
              onClick={() => handleOpen(a.path, a.task_id)}
              onDelete={() => setDeleteTarget({ title: a.title, path: a.path })}
              onRenamed={() => refresh(true)}
            />
          ))}
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
          <HugeiconsIcon icon={FolderOpenIcon} className="h-12 w-12 opacity-20" />
          {archives.length === 0 ? (
            <p>还没有归档结果。处理完成后这里会显示文件。</p>
          ) : (
            <p>没有匹配的结果</p>
          )}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="shrink-0 flex items-center justify-center gap-1 py-1">
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={safePage <= 1}
            onClick={() => setPage(safePage - 1)}
          >
            <HugeiconsIcon icon={ArrowLeft01Icon} className="h-4 w-4" />
          </Button>
          {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
            <Button
              key={p}
              variant={p === safePage ? "default" : "ghost"}
              size="sm"
              className="min-w-8 h-8"
              onClick={() => setPage(p)}
            >
              {p}
            </Button>
          ))}
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={safePage >= totalPages}
            onClick={() => setPage(safePage + 1)}
          >
            <HugeiconsIcon icon={ArrowRight01Icon} className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Delete confirmation dialog */}
      {deleteTarget && (
        <DeleteConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
          title={deleteTarget.title}
          archivePath={deleteTarget.path}
          onDeleted={refresh}
        />
      )}
    </div>
  )
}
