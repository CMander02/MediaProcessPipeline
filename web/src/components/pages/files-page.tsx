import { useState, useCallback, useEffect, useLayoutEffect, useRef } from "react"
import { useArchivePage } from "@/hooks/use-archive-page"
import { usePreferences } from "@/hooks/use-preferences"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { type ArchiveSort, type MediaFilter, type SourceFilter } from "@/lib/archive-filters"
import type { ArchiveItem } from "@/hooks/use-archives"
import { ArchiveCard } from "@/components/archive-card"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { MediaRetentionDialog } from "@/components/media-retention-dialog"
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import { EmptyState, LoadingState } from "@/components/ui/page-state"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { OfflineSyncStatus } from "@/components/offline-sync-status"
import { usePlatform } from "@/platform/use-platform"
import { Button } from "@/components/ui/button"

const PAGE_SIZE = 28
const MIN_PAGE_SIZE = 1

interface FilesPageProps {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
}

export function FilesPage({ search, mediaFilter, sourceFilter, sort }: FilesPageProps) {
  const { capabilities, online } = useAppAccess()
  const platform = usePlatform()
  const { update: updatePrefs } = usePreferences()
  const filterKey = JSON.stringify([search, mediaFilter, sourceFilter, sort])
  const [pagination, setPagination] = useState({ filterKey, page: 1 })
  if (pagination.filterKey !== filterKey) setPagination({ filterKey, page: 1 })
  const page = pagination.filterKey === filterKey ? pagination.page : 1
  const setPage = useCallback((value: number) => setPagination({ filterKey, page: value }), [filterKey])
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const { archives, total, page: resolvedPage, loading, error, indexing, lastReconciledAt,
    refresh, removeArchive } = useArchivePage({ page, page_size: pageSize, search,
    media: mediaFilter, source: sourceFilter, sort })
  const [checking, setChecking] = useState(false)
  const [paginationRangeSize, setPaginationRangeSize] = useState(7)
  const [deleteTarget, setDeleteTarget] = useState<{ title: string; path: string; taskId?: string; taskDelete?: boolean } | null>(null)
  const [retentionTarget, setRetentionTarget] = useState<{ title: string; path: string } | null>(null)
  const [rerunningPath, setRerunningPath] = useState<string | null>(null)
  const [checkpointRerunningPath, setCheckpointRerunningPath] = useState<string | null>(null)
  const [taskActionPath, setTaskActionPath] = useState<string | null>(null)
  const gridRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!loading && resolvedPage !== page) setPage(resolvedPage)
  }, [loading, page, resolvedPage, setPage])

  // While any task is processing, poll for updates so the queue card progress stays fresh
  const anyProcessing = archives.some((a) => a.processing)
  useEffect(() => {
    if (!anyProcessing && !indexing) return
    const id = window.setInterval(() => { refresh(true) }, 3000)
    return () => window.clearInterval(id)
  }, [anyProcessing, indexing, refresh])

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(resolvedPage, totalPages)

  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!grid) return

    const updatePageSize = () => {
      const firstCard = grid.firstElementChild
      if (!(firstCard instanceof HTMLElement)) return

      const styles = window.getComputedStyle(grid)
      const columns = styles.gridTemplateColumns.split(" ").filter(Boolean).length || 1
      setPaginationRangeSize(window.innerWidth >= 768 ? 7 : 3)
      const rowGap = Number.parseFloat(styles.rowGap) || 0
      const cardHeight = firstCard.getBoundingClientRect().height
      if (cardHeight <= 0) return

      const availableHeight = grid.getBoundingClientRect().height
      const rowHeight = cardHeight + rowGap
      const rows = Math.max(1, Math.floor((availableHeight + rowGap) / rowHeight))
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
  }, [archives.length, total])

  const checkFiles = async () => {
    setChecking(true)
    try {
      await api.archives.reconcile()
      await refresh(true)
    } catch (reason) {
      window.alert(`检查文件失败：${String(reason)}`)
    } finally {
      setChecking(false)
    }
  }

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

  if (loading && !retentionTarget) {
    return <LoadingState title="正在加载文件" className="h-full" />
  }

  return (
    <div className={cn(
      "grid h-full min-h-0 gap-2 px-3 pt-3 pb-1 sm:px-4",
      platform.isNative ? "grid-rows-[auto_minmax(0,1fr)_auto]" : "grid-rows-[minmax(0,1fr)_auto]",
    )}>
      {platform.isNative && <OfflineSyncStatus compact />}
      {/* Grid */}
      {archives.length > 0 ? (
        <div
          ref={gridRef}
          data-testid="archive-grid"
          className="grid h-full min-h-0 grid-cols-2 content-start gap-3 overflow-hidden sm:gap-x-5 sm:gap-y-4 lg:grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] min-[1972px]:grid-cols-7!"
        >
          {archives.map((a) => (
            <ArchiveCard
              key={a.path}
              archive={a}
              compact
              onClick={() => handleOpen(a.path, a.task_id)}
              onDelete={capabilities.archive_mutation ? () => setDeleteTarget({
                title: a.title,
                path: a.path,
                taskId: a.task_id,
                taskDelete: Boolean(a.processing && a.task_id),
              }) : undefined}
              onRenamed={capabilities.archive_mutation ? () => refresh(true) : undefined}
              onMediaRetention={capabilities.archive_mutation && !platform.isNative
                ? () => setRetentionTarget(a) : undefined}
              onRerun={online ? () => handleRerun(a) : undefined}
              onCheckpointRerun={online && a.task_id ? () => handleCheckpointRerun(a) : undefined}
              onPause={online && a.task_id ? () => handleTaskAction(a, "pause") : undefined}
              onResume={online && a.task_id ? () => handleTaskAction(a, "resume") : undefined}
              rerunning={rerunningPath === a.path}
              checkpointRerunning={checkpointRerunningPath === a.path}
              taskActionBusy={taskActionPath === a.path}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          className="h-full min-h-0"
          title={error ? "文件加载失败" : search || mediaFilter !== "all" || sourceFilter !== "all" ? "没有匹配的结果" : "还没有归档结果"}
          description={error ?? (search || mediaFilter !== "all" || sourceFilter !== "all" ? "调整搜索条件或筛选项后再试。" : "处理完成后，文件会显示在这里。")}
        />
      )}

      {/* Pagination */}
      <div className="relative flex h-8 min-w-0 items-center gap-1">
      {!platform.isNative && <Button size="sm" variant="ghost" disabled={checking || indexing}
        title={lastReconciledAt ? `上次检查：${new Date(lastReconciledAt).toLocaleString()}。检查文件可发现外部编辑。` : "检查文件可发现外部编辑。"}
        onClick={checkFiles} className="shrink-0 px-1 text-xs">
        {checking || indexing ? "检查中…" : "检查文件"}
      </Button>}
      {totalPages > 1 && (
        <Pagination data-testid="files-pagination" className="h-8 min-w-0 flex-1">
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
      {!platform.isNative && <span className="ml-auto hidden shrink-0 text-xs text-muted-foreground lg:block">
        {lastReconciledAt ? `上次检查 ${new Date(lastReconciledAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "等待首次检查"}
      </span>}
      </div>

      {/* Delete confirmation dialog */}
      {retentionTarget && <MediaRetentionDialog archive={retentionTarget}
        onClose={() => setRetentionTarget(null)} onApplied={() => void refresh(true)} />}
      {deleteTarget && (
        <DeleteConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
          title={deleteTarget.title}
          archivePath={deleteTarget.path}
          taskId={deleteTarget.taskId}
          taskDelete={deleteTarget.taskDelete}
          onDeleted={removeArchive}
        />
      )}
    </div>
  )
}

function getPaginationItems(currentPage: number, totalPages: number, rangeSize: number): Array<number | "ellipsis"> {
  if (totalPages <= rangeSize + 2) return Array.from({ length: totalPages }, (_, index) => index + 1)

  const half = Math.floor(rangeSize / 2)
  let start = Math.max(1, currentPage - half)
  const end = Math.min(totalPages, start + rangeSize - 1)
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
