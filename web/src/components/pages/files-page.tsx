import { useState, useMemo, useEffect, useLayoutEffect, useRef } from "react"
import { useArchives } from "@/hooks/use-archives"
import { usePreferences } from "@/hooks/use-preferences"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { sourceFilterFromMetadata, type MediaFilter, type SourceFilter } from "@/lib/archive-filters"
import type { ArchiveItem } from "@/hooks/use-archives"
import { ArchiveCard } from "@/components/archive-card"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon, FolderOpenIcon, ArrowLeft01Icon, ArrowRight01Icon } from "@hugeicons/core-free-icons"

const PAGE_SIZE = 24
const MIN_PAGE_SIZE = 1

interface FilesPageProps {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
}

export function FilesPage({ search, mediaFilter, sourceFilter }: FilesPageProps) {
  const { archives, loading, refresh, removeArchive } = useArchives()
  const { update: updatePrefs } = usePreferences()
  const [page, setPage] = useState(1)
  const [pageInput, setPageInput] = useState("1")
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [deleteTarget, setDeleteTarget] = useState<{ title: string; path: string; taskId?: string; taskDelete?: boolean } | null>(null)
  const [rerunningPath, setRerunningPath] = useState<string | null>(null)
  const [checkpointRerunningPath, setCheckpointRerunningPath] = useState<string | null>(null)
  const [taskActionPath, setTaskActionPath] = useState<string | null>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const paginationRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    let list = archives
    if (mediaFilter === "video") list = list.filter((a) => a.has_video)
    if (mediaFilter === "audio") list = list.filter((a) => !a.has_video && !a.has_image && a.has_audio)
    if (mediaFilter === "image") {
      list = list.filter((a) => {
        const subtype = a.metadata?.content_subtype
        return a.has_image || subtype === "image_note" || subtype === "text_note"
      })
    }
    if (sourceFilter !== "all") {
      list = list.filter((a) => sourceFilterFromMetadata(a.metadata) === sourceFilter)
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter((a) => a.title.toLowerCase().includes(q))
    }
    // Surface in-progress tasks at the top so the user sees their just-submitted work first
    return [...list].sort((a, b) => {
      if (!!a.processing !== !!b.processing) return a.processing ? -1 : 1
      return 0
    })
  }, [archives, search, mediaFilter, sourceFilter])

  // Reset page when filters change
  useEffect(() => { setPage(1) }, [search, mediaFilter, sourceFilter])

  // While any task is processing, poll for updates so the queue card progress stays fresh
  const anyProcessing = archives.some((a) => a.processing)
  useEffect(() => {
    if (!anyProcessing) return
    const id = window.setInterval(() => { refresh(true) }, 3000)
    return () => window.clearInterval(id)
  }, [anyProcessing, refresh])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const paged = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!grid) return

    const updatePageSize = () => {
      const firstCard = grid.firstElementChild
      if (!(firstCard instanceof HTMLElement)) return

      const styles = window.getComputedStyle(grid)
      const columns = styles.gridTemplateColumns.split(" ").filter(Boolean).length || 1
      const rowGap = Number.parseFloat(styles.rowGap) || 0
      const cardHeight = firstCard.getBoundingClientRect().height
      if (cardHeight <= 0) return

      const gridTop = grid.getBoundingClientRect().top
      const paginationHeight = paginationRef.current?.getBoundingClientRect().height ?? 32
      const availableHeight = window.innerHeight - gridTop - paginationHeight - 12
      const rows = Math.max(1, Math.floor((availableHeight + rowGap) / (cardHeight + rowGap)))
      const nextPageSize = Math.max(MIN_PAGE_SIZE, columns * rows)
      setPageSize((current) => (current === nextPageSize ? current : nextPageSize))
    }

    updatePageSize()
    const observer = new ResizeObserver(updatePageSize)
    observer.observe(grid)
    window.addEventListener("resize", updatePageSize)
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", updatePageSize)
    }
  }, [paged.length, filtered.length])

  useEffect(() => {
    setPageInput(String(safePage))
  }, [safePage])

  const handleOpen = (path: string, taskId?: string) => {
    updatePrefs({ lastArchivePath: path })
    const tid = taskId ? `&taskId=${encodeURIComponent(taskId)}` : ""
    navigate(`#/result/archive?path=${encodeURIComponent(path)}${tid}`)
  }

  const sourceFromMetadata = (archive: ArchiveItem): string => {
    const metadata = archive.metadata ?? {}
    const extra = metadata.extra
    const candidates = [
      metadata.source_url,
      metadata.original_url,
      metadata.webpage_url,
      metadata.file_path,
      extra && typeof extra === "object" && "original_url" in extra ? (extra as Record<string, unknown>).original_url : undefined,
      extra && typeof extra === "object" && "webpage_url" in extra ? (extra as Record<string, unknown>).webpage_url : undefined,
    ]
    for (const candidate of candidates) {
      if (typeof candidate === "string" && candidate.trim()) return candidate.trim()
    }
    return ""
  }

  const handleRerun = async (archive: ArchiveItem) => {
    if (rerunningPath) return
    setRerunningPath(archive.path)
    try {
      let source = ""
      let options: Record<string, unknown> = {}
      if (archive.task_id) {
        try {
          const task = await api.tasks.get(archive.task_id)
          source = task.source
          options = task.options ?? {}
        } catch {
          source = ""
        }
      }
      if (!source) source = sourceFromMetadata(archive)
      if (!source) {
        window.alert("找不到原始来源，无法重做。")
        return
      }
      await api.tasks.create(source, options)
      setPage(1)
      await refresh(true)
    } catch (e) {
      window.alert(`重做失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setRerunningPath(null)
    }
  }

  const handleCheckpointRerun = async (archive: ArchiveItem) => {
    if (!archive.task_id || checkpointRerunningPath) return
    setCheckpointRerunningPath(archive.path)
    try {
      const task = await api.tasks.get(archive.task_id)
      if (task.status === "queued" || task.status === "processing") {
        handleOpen(archive.path, archive.task_id)
        return
      }
      await api.tasks.checkpointRerun(archive.task_id)
      setPage(1)
      await refresh(true)
      navigate(`#/result/task/${archive.task_id}`)
    } catch (e) {
      window.alert(`断点续做失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setCheckpointRerunningPath(null)
    }
  }

  const handleTaskAction = async (archive: ArchiveItem, action: "pause" | "resume") => {
    if (!archive.task_id || taskActionPath) return
    setTaskActionPath(archive.path)
    try {
      if (action === "pause") await api.tasks.pause(archive.task_id)
      if (action === "resume") await api.tasks.resume(archive.task_id)
      await refresh(true)
    } catch (e) {
      window.alert(`${action === "pause" ? "暂停" : "恢复"}失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setTaskActionPath(null)
    }
  }

  const commitPageInput = () => {
    const parsed = Number.parseInt(pageInput, 10)
    if (!Number.isFinite(parsed)) {
      setPageInput(String(safePage))
      return
    }
    const nextPage = Math.min(Math.max(parsed, 1), totalPages)
    setPage(nextPage)
    setPageInput(String(nextPage))
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <HugeiconsIcon icon={Loading03Icon} className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-2 px-3 pt-3 pb-1 sm:px-4">
      {/* Grid */}
      {filtered.length > 0 ? (
        <div
          ref={gridRef}
          data-testid="archive-grid"
          className="grid h-full min-h-0 grid-cols-2 content-between gap-x-3 gap-y-2 overflow-hidden sm:grid-cols-[repeat(auto-fill,minmax(min(220px,100%),1fr))] sm:gap-x-4 sm:gap-y-3"
        >
          {paged.map((a) => (
            <ArchiveCard
              key={a.path}
              archive={a}
              compact
              onClick={() => handleOpen(a.path, a.task_id)}
              onDelete={() => setDeleteTarget({
                title: a.title,
                path: a.path,
                taskId: a.task_id,
                taskDelete: Boolean(a.processing && a.task_id),
              })}
              onRenamed={() => refresh(true)}
              onRerun={() => handleRerun(a)}
              onCheckpointRerun={a.task_id ? () => handleCheckpointRerun(a) : undefined}
              onPause={a.task_id ? () => handleTaskAction(a, "pause") : undefined}
              onResume={a.task_id ? () => handleTaskAction(a, "resume") : undefined}
              rerunning={rerunningPath === a.path}
              checkpointRerunning={checkpointRerunningPath === a.path}
              taskActionBusy={taskActionPath === a.path}
            />
          ))}
        </div>
      ) : (
        <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 text-muted-foreground">
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
        <div
          ref={paginationRef}
          data-testid="files-pagination"
          className="h-8 shrink-0 flex items-center justify-center gap-2"
        >
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={safePage <= 1}
            onClick={() => setPage(safePage - 1)}
            aria-label="上一页"
            title="上一页"
          >
            <HugeiconsIcon icon={ArrowLeft01Icon} className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Input
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value.replace(/[^\d]/g, ""))}
              onBlur={commitPageInput}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur()
                }
              }}
              inputMode="numeric"
              className="h-8 w-14 px-2 text-center text-sm text-foreground"
              aria-label="页码"
            />
            <span>/</span>
            <span className="min-w-6 text-foreground">{totalPages}</span>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={safePage >= totalPages}
            onClick={() => setPage(safePage + 1)}
            aria-label="下一页"
            title="下一页"
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
          taskId={deleteTarget.taskId}
          taskDelete={deleteTarget.taskDelete}
          onDeleted={() => removeArchive(deleteTarget.path)}
        />
      )}
    </div>
  )
}
