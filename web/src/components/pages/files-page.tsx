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
import { archiveGridLayout } from "@/lib/archive-grid-layout"
import { RerunConfirmDialog } from "@/components/rerun-confirm-dialog"
import { notifyError, notifySuccess } from "@/lib/notify"

const PAGE_SIZE = 28

interface FilesPageProps {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
  /** Current page, kept in the URL so 返回 / reload restore it */
  page?: number
  onPageChange?: (page: number) => void
}

// Archive opened from the grid; focused again when the library remounts after 返回.
let returnFocusPath: string | null = null

export function FilesPage({ search, mediaFilter, sourceFilter, sort, page: pageProp, onPageChange }: FilesPageProps) {
  const { capabilities, online } = useAppAccess()
  const platform = usePlatform()
  const { update: updatePrefs } = usePreferences()
  // Fallback local page state for embeddings that don't route the page through the URL (tests).
  const filterKey = JSON.stringify([search, mediaFilter, sourceFilter, sort])
  const [pagination, setPagination] = useState({ filterKey, page: 1 })
  if (!onPageChange && pagination.filterKey !== filterKey) setPagination({ filterKey, page: 1 })
  const page = onPageChange ? (pageProp ?? 1) : (pagination.filterKey === filterKey ? pagination.page : 1)
  const setPage = useCallback((value: number) => {
    if (onPageChange) onPageChange(value)
    else setPagination({ filterKey, page: value })
  }, [filterKey, onPageChange])
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [rerunTarget, setRerunTarget] = useState<ArchiveItem | null>(null)
  const [gridLayout, setGridLayout] = useState<{ columns: number; rowGap: number } | null>(null)
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

  // Coming back from a result page: focus the card that was opened so the eye lands where it left.
  useEffect(() => {
    if (loading || !returnFocusPath) return
    const target = gridRef.current?.querySelector<HTMLElement>(`[data-archive-path="${CSS.escape(returnFocusPath)}"]`)
    returnFocusPath = null
    if (target) {
      target.focus({ preventScroll: true })
      target.scrollIntoView({ block: "nearest" })
    }
  }, [loading, archives])

  // While a task is actively running, poll so card progress stays fresh. Paused tasks don't change.
  const anyProcessing = archives.some((a) => a.processing && a.metadata?.status !== "paused")
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

      setPaginationRangeSize(window.innerWidth >= 768 ? 7 : 3)
      const info = firstCard.querySelector<HTMLElement>("[data-archive-info]")
      if (!info) return
      const { width, height } = grid.getBoundingClientRect()
      if (width <= 0 || height <= 0) return
      const next = archiveGridLayout(width, height, info.getBoundingClientRect().height, window.innerWidth)
      setGridLayout((current) => current?.columns === next.columns && current.rowGap === next.rowGap
        ? current : { columns: next.columns, rowGap: next.rowGap })
      setPageSize((current) => current === next.pageSize ? current : next.pageSize)
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
  }, [archives.length, total, loading])

  const checkFiles = async () => {
    setChecking(true)
    try {
      await api.archives.reconcile()
      await refresh(true)
    } catch (reason) {
      notifyError("检查文件失败", reason)
    } finally {
      setChecking(false)
    }
  }

  const handleOpen = (path: string, taskId?: string) => {
    updatePrefs({ lastArchivePath: path })
    returnFocusPath = path
    const tid = taskId ? `&taskId=${encodeURIComponent(taskId)}` : ""
    navigate(`#/result/archive?path=${encodeURIComponent(path)}${tid}`)
  }

  const handleRerun = async (archive: ArchiveItem) => {
    if (rerunningPath) return
    if (!archive.task_id) {
      notifyError("无法重新处理", "找不到这个条目的任务记录。")
      return
    }
    setRerunningPath(archive.path)
    try {
      await api.tasks.fullRerun(archive.task_id)
      notifySuccess("已加入处理队列", `「${archive.title}」将从头重新处理。`)
      await refresh(true)
    } catch (e) {
      notifyError("重新处理失败", e)
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
        await refresh(true)
        return
      }
      await api.tasks.checkpointRerun(archive.task_id)
      notifySuccess("已加入处理队列", `「${archive.title}」会跳过已完成的步骤。`)
      await refresh(true)
    } catch (e) {
      notifyError("加入队列失败", e)
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
      notifyError(action === "pause" ? "暂停失败" : "继续处理失败", e)
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
          className="grid h-full min-h-0 grid-cols-2 content-start items-start gap-3 overflow-x-hidden overflow-y-auto sm:gap-x-5 sm:gap-y-4 lg:grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] min-[1972px]:grid-cols-7"
          style={gridLayout ? { gridTemplateColumns: `repeat(${gridLayout.columns}, minmax(0, 1fr))`, rowGap: gridLayout.rowGap } : undefined}
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
              onRerun={online && a.task_id ? () => setRerunTarget(a) : undefined}
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
      <RerunConfirmDialog
        open={rerunTarget !== null}
        onOpenChange={(open) => { if (!open) setRerunTarget(null) }}
        title={rerunTarget?.title ?? ""}
        onConfirm={() => {
          const target = rerunTarget
          setRerunTarget(null)
          if (target) void handleRerun(target)
        }}
      />
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
