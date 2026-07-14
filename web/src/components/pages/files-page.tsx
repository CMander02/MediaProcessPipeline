import { useState, useMemo, useEffect, useLayoutEffect, useRef } from "react"
import { useArchives } from "@/hooks/use-archives"
import { usePreferences } from "@/hooks/use-preferences"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { sourceFilterFromMetadata, type MediaFilter, type SourceFilter } from "@/lib/archive-filters"
import type { ArchiveItem } from "@/hooks/use-archives"
import { ArchiveCard } from "@/components/archive-card"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon, FolderOpenIcon } from "@hugeicons/core-free-icons"

const PAGE_SIZE = 24
const MIN_PAGE_SIZE = 1

interface FilesPageProps {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
}

export type ArchiveSort = "created_desc" | "created_asc" | "published_desc" | "title_asc"

export function FilesPage({ search, mediaFilter, sourceFilter, sort }: FilesPageProps) {
  const { archives, loading, refresh, removeArchive } = useArchives()
  const { update: updatePrefs } = usePreferences()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [paginationRangeSize, setPaginationRangeSize] = useState(7)
  const [deleteTarget, setDeleteTarget] = useState<{ title: string; path: string; taskId?: string; taskDelete?: boolean } | null>(null)
  const [rerunningPath, setRerunningPath] = useState<string | null>(null)
  const [checkpointRerunningPath, setCheckpointRerunningPath] = useState<string | null>(null)
  const [taskActionPath, setTaskActionPath] = useState<string | null>(null)
  const gridRef = useRef<HTMLDivElement>(null)

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
    const timestamp = (value: unknown) => {
      if (typeof value !== "string" || !value) return 0
      const parsed = new Date(value).getTime()
      return Number.isFinite(parsed) ? parsed : 0
    }

    // Surface active work first, then apply the selected archive ordering.
    return [...list].sort((a, b) => {
      if (!!a.processing !== !!b.processing) return a.processing ? -1 : 1
      if (sort === "created_asc") return timestamp(a.created_at) - timestamp(b.created_at)
      if (sort === "published_desc") return timestamp(b.metadata?.upload_date) - timestamp(a.metadata?.upload_date)
      if (sort === "title_asc") return a.title.localeCompare(b.title, "zh-CN")
      return timestamp(b.created_at) - timestamp(a.created_at)
    })
  }, [archives, search, mediaFilter, sourceFilter, sort])

  // Reset page when filters change
  useEffect(() => { setPage(1) }, [search, mediaFilter, sourceFilter, sort])

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
      setPaginationRangeSize(grid.clientWidth >= 768 ? 7 : 3)
      const rowGap = Number.parseFloat(styles.rowGap) || 0
      const cardHeight = firstCard.getBoundingClientRect().height
      if (cardHeight <= 0) return

      const availableHeight = grid.getBoundingClientRect().height
      const rowHeight = cardHeight + rowGap
      const rows = Math.max(1, Math.floor((availableHeight + rowGap + cardHeight * 0.2) / rowHeight))
      const nextPageSize = Math.max(MIN_PAGE_SIZE, columns * rows)
      setPageSize((current) => (current === nextPageSize ? current : nextPageSize))
    }

    updatePageSize()
    const observer = new ResizeObserver(updatePageSize)
    observer.observe(grid)
    const firstCard = grid.firstElementChild
    if (firstCard instanceof HTMLElement) observer.observe(firstCard)
    const frame = window.requestAnimationFrame(updatePageSize)
    window.addEventListener("resize", updatePageSize)
    return () => {
      window.cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener("resize", updatePageSize)
    }
  }, [paged.length, filtered.length])

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

  const pageItems = getPaginationItems(safePage, totalPages, paginationRangeSize)

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
          className="grid h-full min-h-0 grid-cols-2 content-start gap-3 overflow-hidden sm:grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] sm:gap-x-5 sm:gap-y-4"
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
        <Pagination data-testid="files-pagination" className="h-8 shrink-0">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                href="#"
                aria-disabled={safePage <= 1}
                tabIndex={safePage <= 1 ? -1 : undefined}
                className={safePage <= 1 ? "pointer-events-none opacity-50" : undefined}
                onClick={(event) => { event.preventDefault(); if (safePage > 1) setPage(safePage - 1) }}
              />
            </PaginationItem>
            {pageItems.map((item, index) => item === "ellipsis" ? (
              <PaginationItem key={`ellipsis-${index}`}>
                <PaginationEllipsis />
              </PaginationItem>
            ) : (
              <PaginationItem key={item}>
                <PaginationLink
                  href="#"
                  isActive={item === safePage}
                  aria-label={`第 ${item} 页`}
                  onClick={(event) => { event.preventDefault(); setPage(item) }}
                >
                  {item}
                </PaginationLink>
              </PaginationItem>
            ))}
            <PaginationItem>
              <PaginationNext
                href="#"
                aria-disabled={safePage >= totalPages}
                tabIndex={safePage >= totalPages ? -1 : undefined}
                className={safePage >= totalPages ? "pointer-events-none opacity-50" : undefined}
                onClick={(event) => { event.preventDefault(); if (safePage < totalPages) setPage(safePage + 1) }}
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
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

function getPaginationItems(currentPage: number, totalPages: number, rangeSize: number): Array<number | "ellipsis"> {
  if (totalPages <= rangeSize + 2) return Array.from({ length: totalPages }, (_, index) => index + 1)

  const half = Math.floor(rangeSize / 2)
  let start = Math.max(1, currentPage - half)
  let end = Math.min(totalPages, start + rangeSize - 1)
  start = Math.max(1, end - rangeSize + 1)

  const pages = new Set<number>([1, totalPages])
  for (let page = start; page <= end; page += 1) pages.add(page)
  const sorted = [...pages].sort((a, b) => a - b)
  const items: Array<number | "ellipsis"> = []
  for (const page of sorted) {
    const previous = items.at(-1)
    if (typeof previous === "number" && page - previous > 1) items.push("ellipsis")
    items.push(page)
  }
  return items
}
