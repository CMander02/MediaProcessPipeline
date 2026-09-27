import { useState, useCallback, useEffect, useLayoutEffect, useMemo, useRef, type MouseEvent as ReactMouseEvent } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { CheckListIcon, Loading03Icon, MoreHorizontalIcon } from "@hugeicons/core-free-icons"
import { useArchivePage } from "@/hooks/use-archive-page"
import { usePreferences } from "@/hooks/use-preferences"
import { useActiveTasks, refreshActiveTasks } from "@/hooks/use-active-tasks"
import { useMediaQuery } from "@/hooks/use-media-query"
import { navigate } from "@/lib/router"
import { api, type Task } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  type ArchiveSort,
  type MediaFilter,
  type SourceFilter,
  type StatusFilter,
} from "@/lib/archive-filters"
import { archiveStatus } from "@/lib/archive-query"
import { publishLibraryFacets } from "@/lib/library-facets"
import type { ArchiveItem } from "@/hooks/use-archives"
import { ArchiveCard } from "@/components/archive-card"
import { StatusChips } from "@/components/status-chips"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { MediaRetentionDialog } from "@/components/media-retention-dialog"
import { BatchMediaRetentionDialog } from "@/components/batch-media-retention-dialog"
import { archiveExportEntries, uniqueFolders } from "@/lib/archive-export"
import { createZip, downloadBytes } from "@/lib/zip"
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { EmptyState, LoadingState } from "@/components/ui/page-state"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { OfflineSyncStatus } from "@/components/offline-sync-status"
import { usePlatform } from "@/platform/use-platform"
import { Button } from "@/components/ui/button"
import { archiveGridLayout } from "@/lib/archive-grid-layout"
import { RerunConfirmDialog } from "@/components/rerun-confirm-dialog"
import { notifyError, notifySuccess } from "@/lib/notify"

const PAGE_SIZE = 28
const PHONE_BATCH = 12
// Largest page the archive API serves
const MAX_QUERY = 500
const AUTO_CHECK_AFTER_MS = 30 * 60 * 1000

interface FilesPageProps {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
  /** Status chip; kept in the URL like the other filters */
  status?: StatusFilter
  onStatusChange?: (status: StatusFilter) => void
  /** Current page, kept in the URL so 返回 / reload restore it */
  page?: number
  onPageChange?: (page: number) => void
}

// Archive opened from the grid; focused again when the library remounts after 返回.
let returnFocusPath: string | null = null
// External-edit check runs automatically at most once per app session.
let autoCheckDone = false

export function FilesPage({
  search, mediaFilter, sourceFilter, sort, status = "all", onStatusChange, page: pageProp, onPageChange,
}: FilesPageProps) {
  const { capabilities, online } = useAppAccess()
  const platform = usePlatform()
  const isPhone = useMediaQuery("(max-width: 767px)")
  const { update: updatePrefs } = usePreferences()
  // Fallback local page state for embeddings that don't route the page through the URL (tests).
  const filterKey = JSON.stringify([search, mediaFilter, sourceFilter, sort, status])
  const [pagination, setPagination] = useState({ filterKey, page: 1 })
  if (!onPageChange && pagination.filterKey !== filterKey) setPagination({ filterKey, page: 1 })
  const page = onPageChange ? (pageProp ?? 1) : (pagination.filterKey === filterKey ? pagination.page : 1)
  const setPage = useCallback((value: number) => {
    if (onPageChange) onPageChange(value)
    else setPagination({ filterKey, page: value })
  }, [filterKey, onPageChange])
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [phoneState, setPhoneState] = useState({ filterKey, limit: PHONE_BATCH })
  if (phoneState.filterKey !== filterKey) setPhoneState({ filterKey, limit: PHONE_BATCH })
  const phoneLimit = Math.min(MAX_QUERY, phoneState.filterKey === filterKey ? phoneState.limit : PHONE_BATCH)
  const [rerunTarget, setRerunTarget] = useState<ArchiveItem | null>(null)
  const [gridLayout, setGridLayout] = useState<{ columns: number; rowGap: number } | null>(null)
  const query = isPhone
    ? { page: 1, page_size: phoneLimit }
    : { page, page_size: pageSize }
  const { archives, total, page: resolvedPage, loading, error, indexing, lastReconciledAt, facets,
    refresh, removeArchive } = useArchivePage({ ...query, search,
    media: mediaFilter, source: sourceFilter, sort, status })
  const [checking, setChecking] = useState(false)
  const [paginationRangeSize, setPaginationRangeSize] = useState(7)
  const [deleteTarget, setDeleteTarget] = useState<{ title: string; path: string; taskId?: string; taskDelete?: boolean } | null>(null)
  const [retentionTarget, setRetentionTarget] = useState<{ title: string; path: string } | null>(null)
  const [rerunningPath, setRerunningPath] = useState<string | null>(null)
  const [checkpointRerunningPath, setCheckpointRerunningPath] = useState<string | null>(null)
  const [taskActionPath, setTaskActionPath] = useState<string | null>(null)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const anchorIndex = useRef<number | null>(null)
  const [batchBusy, setBatchBusy] = useState<"retry" | "delete" | "export" | null>(null)
  const [confirmBatchDelete, setConfirmBatchDelete] = useState(false)
  const [batchRetention, setBatchRetention] = useState<ArchiveItem[] | null>(null)
  const [retryAll, setRetryAll] = useState<{ items: ArchiveItem[]; done: number; running: boolean } | null>(null)
  const [cleanup, setCleanup] = useState<{ count: number; busy: boolean } | null>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const lastArchives = useRef<ArchiveItem[]>([])
  if (!loading) lastArchives.current = archives
  // On phones "load more" grows one query; keep showing what we had while it reloads.
  const shown = loading && isPhone ? lastArchives.current : archives

  const { tasks: activeTasks } = useActiveTasks()
  const tasksById = useMemo(() => new Map<string, Task>(activeTasks.map((task) => [task.id, task])), [activeTasks])

  useEffect(() => {
    if (isPhone) return
    if (!loading && resolvedPage !== page) setPage(resolvedPage)
  }, [isPhone, loading, page, resolvedPage, setPage])

  useEffect(() => {
    publishLibraryFacets(facets)
  }, [facets])
  useEffect(() => () => publishLibraryFacets(null), [])

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

  // A task leaving the active list has finished, failed or been removed: reload the cards.
  const activeIds = activeTasks.map((task) => task.id).sort().join(",")
  const previousActiveIds = useRef(activeIds)
  useEffect(() => {
    if (previousActiveIds.current !== activeIds) {
      previousActiveIds.current = activeIds
      void refresh(true)
    }
  }, [activeIds, refresh])

  // While a task is actively running, poll so card state stays fresh. Paused tasks don't change.
  const anyProcessing = shown.some((a) => a.processing && a.metadata?.status !== "paused")
  useEffect(() => {
    if (!anyProcessing && !indexing) return
    const id = window.setInterval(() => { refresh(true) }, 5000)
    return () => window.clearInterval(id)
  }, [anyProcessing, indexing, refresh])

  // Files edited outside MPP show up without anyone pressing a button.
  useEffect(() => {
    if (autoCheckDone || loading || platform.isNative || !online) return
    const last = lastReconciledAt ? Date.parse(lastReconciledAt) : 0
    if (last && Date.now() - last < AUTO_CHECK_AFTER_MS) return
    autoCheckDone = true
    void (async () => {
      try {
        await api.archives.reconcile()
        await refresh(true)
      } catch {
        // A failed background check is retried next session or from the ⋯ menu.
      }
    })()
  }, [lastReconciledAt, loading, online, platform.isNative, refresh])

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(resolvedPage, totalPages)

  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!grid) return
    if (isPhone) {
      setGridLayout(null)
      return
    }

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
  }, [archives.length, total, loading, isPhone])

  // Phones scroll through the whole library instead of paging.
  useEffect(() => {
    const sentinel = sentinelRef.current
    const grid = gridRef.current
    if (!isPhone || !sentinel || !grid || typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loading && shown.length < total && phoneLimit < MAX_QUERY) {
        setPhoneState({ filterKey, limit: phoneLimit + PHONE_BATCH })
      }
    }, { root: grid, rootMargin: "400px" })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [filterKey, isPhone, loading, phoneLimit, shown.length, total])

  // ---------- selection ----------
  const exitSelection = useCallback(() => {
    setSelectionMode(false)
    setSelected(new Set())
    anchorIndex.current = null
  }, [])

  useEffect(() => {
    exitSelection()
  }, [filterKey, page, exitSelection])

  const toggleSelect = (index: number, event: ReactMouseEvent) => {
    const path = shown[index]?.path
    if (!path) return
    setSelectionMode(true)
    setSelected((current) => {
      const next = new Set(current)
      if (event.shiftKey && anchorIndex.current !== null) {
        const [from, to] = [anchorIndex.current, index].sort((a, b) => a - b)
        for (let i = from; i <= to; i += 1) next.add(shown[i].path)
      } else if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
    anchorIndex.current = index
  }

  useEffect(() => {
    if (!selectionMode) return
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest("input, textarea, [contenteditable=true]")) return
      if (event.key === "Escape") exitSelection()
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
        event.preventDefault()
        setSelected(new Set(shown.map((item) => item.path)))
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [exitSelection, selectionMode, shown])

  const selectedItems = shown.filter((item) => selected.has(item.path))
  const retryable = selectedItems.filter((item) => {
    const state = archiveStatus(item)
    return Boolean(item.task_id) && (state === "failed" || state === "paused")
  })

  const runBatchRetry = async () => {
    if (batchBusy || retryable.length === 0) return
    setBatchBusy("retry")
    let done = 0
    const failures: string[] = []
    for (const item of retryable) {
      try {
        if (archiveStatus(item) === "paused") await api.tasks.resume(item.task_id!)
        else await api.tasks.checkpointRerun(item.task_id!)
        done += 1
      } catch {
        failures.push(item.title)
      }
    }
    setBatchBusy(null)
    if (done) notifySuccess(`${done} 项已加入处理队列`)
    if (failures.length) notifyError(`${failures.length} 项没能加入队列`, failures.slice(0, 3).join("、"))
    exitSelection()
    void refreshActiveTasks()
    await refresh(true)
  }

  const runBatchDelete = async () => {
    setConfirmBatchDelete(false)
    if (batchBusy || selectedItems.length === 0) return
    setBatchBusy("delete")
    let done = 0
    const failures: string[] = []
    for (const item of selectedItems) {
      try {
        if (item.processing && item.task_id) await api.tasks.delete(item.task_id)
        else await api.archives.delete(item.path)
        done += 1
      } catch {
        failures.push(item.title)
      }
    }
    setBatchBusy(null)
    if (done) notifySuccess(`已删除 ${done} 项`)
    if (failures.length) notifyError(`${failures.length} 项没能删除`, failures.slice(0, 3).join("、"))
    exitSelection()
    removeArchive()
  }

  const runBatchExport = async () => {
    if (batchBusy || selectedItems.length === 0) return
    setBatchBusy("export")
    try {
      const folders = uniqueFolders(selectedItems.map((item) => item.title))
      const entries = (await Promise.all(selectedItems.map((item, index) => archiveExportEntries(item, folders[index])))).flat()
      if (entries.length === 0) {
        notifyError("没有可导出的内容", "选中的条目还没有摘要或字幕。")
        return
      }
      const stamp = new Date().toISOString().slice(0, 10)
      downloadBytes(`MPP 导出 ${stamp}（${selectedItems.length} 项）.zip`, createZip(entries))
      notifySuccess(`已导出 ${selectedItems.length} 项`, "每项一个文件夹：摘要、导图、字幕（SRT 和纯文本）。")
    } catch (reason) {
      notifyError("导出失败", reason)
    } finally {
      setBatchBusy(null)
    }
  }

  // ---------- retry every failed item in the current filter ----------
  const openRetryAll = async () => {
    try {
      const found: ArchiveItem[] = []
      for (let next = 1; ; next += 1) {
        const result = await api.archives.page({
          page: next, page_size: MAX_QUERY, search, media: mediaFilter, source: sourceFilter, sort, status: "failed",
        })
        found.push(...(result.archives as ArchiveItem[]))
        if (found.length >= result.total || result.archives.length === 0) break
      }
      const items = found.filter((item) => item.task_id)
      if (items.length === 0) {
        notifySuccess("没有可以重试的失败条目")
        return
      }
      setRetryAll({ items, done: 0, running: false })
    } catch (reason) {
      notifyError("读取失败条目失败", reason)
    }
  }

  const runRetryAll = async () => {
    if (!retryAll || retryAll.running) return
    const items = retryAll.items
    setRetryAll({ items, done: 0, running: true })
    let done = 0
    const failures: string[] = []
    for (const item of items) {
      try {
        await api.tasks.checkpointRerun(item.task_id!)
      } catch {
        failures.push(item.title)
      }
      done += 1
      setRetryAll({ items, done, running: true })
    }
    setRetryAll(null)
    const queued = items.length - failures.length
    if (queued) notifySuccess(`${queued} 项已从断点重新排队`)
    if (failures.length) notifyError(`${failures.length} 项没能加入队列`, failures.slice(0, 3).join("、"))
    void refreshActiveTasks()
    await refresh(true)
  }

  // ---------- duplicate cleanup ----------
  const openCleanup = async () => {
    try {
      const preview = await api.archives.duplicateCleanupPreview()
      if (preview.count === 0) {
        notifySuccess("没有可以清理的失败副本", "重复的来源都还没有成功处理过的版本。")
        return
      }
      setCleanup({ count: preview.count, busy: false })
    } catch (reason) {
      notifyError("读取重复条目失败", reason)
    }
  }

  const runCleanup = async () => {
    if (!cleanup) return
    setCleanup({ ...cleanup, busy: true })
    try {
      const result = await api.archives.cleanupDuplicates()
      notifySuccess(`已清理 ${result.deleted} 个失败副本`)
      if (result.errors.length) notifyError(`${result.errors.length} 个没能删除`, result.errors.slice(0, 3).map((item) => item.title).join("、"))
      removeArchive()
    } catch (reason) {
      notifyError("清理失败副本失败", reason)
    } finally {
      setCleanup(null)
    }
  }

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
  const statusCounts = facets?.status
  const filtered = Boolean(search.trim()) || mediaFilter !== "all" || sourceFilter !== "all" || status !== "all"

  if (loading && !retentionTarget && shown.length === 0) {
    return <LoadingState title="正在加载文件" className="h-full" />
  }

  return (
    <div className={cn(
      "grid h-full min-h-0 gap-2 px-3 pt-3 pb-1 sm:px-4",
      platform.isNative
        ? (isPhone ? "grid-rows-[auto_auto_minmax(0,1fr)_auto]" : "grid-rows-[auto_minmax(0,1fr)_auto]")
        : (isPhone ? "grid-rows-[auto_minmax(0,1fr)_auto]" : "grid-rows-[minmax(0,1fr)_auto]"),
    )}>
      {platform.isNative && <OfflineSyncStatus compact />}

      {/* On phones the status chips live in the page; on larger screens they sit in the header */}
      {isPhone && (
        <StatusChips status={status} onChange={(next) => onStatusChange?.(next)} counts={statusCounts} className="-mx-1 px-1" />
      )}

      {/* Grid */}
      {shown.length > 0 ? (
        <div
          ref={gridRef}
          data-testid="archive-grid"
          className="grid h-full min-h-0 grid-cols-2 content-start items-start gap-3 overflow-x-hidden overflow-y-auto max-md:*:[contain-intrinsic-size:auto_220px] max-md:*:[content-visibility:auto] sm:gap-x-5 sm:gap-y-4 lg:grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] min-[1972px]:grid-cols-7"
          style={gridLayout ? { gridTemplateColumns: `repeat(${gridLayout.columns}, minmax(0, 1fr))`, rowGap: gridLayout.rowGap } : undefined}
        >
          {shown.map((a, index) => (
            <ArchiveCard
              key={a.path}
              archive={a}
              compact
              task={a.task_id ? tasksById.get(a.task_id) : undefined}
              selectionMode={selectionMode}
              selected={selected.has(a.path)}
              onSelect={platform.isNative ? undefined : (event) => toggleSelect(index, event)}
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
          {isPhone && (
            <div ref={sentinelRef} className="col-span-full flex min-h-10 items-center justify-center py-2 text-xs text-muted-foreground">
              {shown.length < total
                ? (loading ? "正在加载…" : phoneLimit >= MAX_QUERY ? `已显示前 ${shown.length} 项，用搜索或筛选缩小范围` : `已显示 ${shown.length} / ${total}`)
                : `共 ${total} 项`}
            </div>
          )}
        </div>
      ) : (
        <EmptyState
          className="h-full min-h-0"
          title={error ? "文件加载失败" : filtered ? "没有匹配的结果" : "还没有归档结果"}
          description={error ?? (filtered ? "调整搜索条件或筛选项后再试。" : "处理完成后，文件会显示在这里。")}
        />
      )}

      {/* Selection actions replace pagination while selecting */}
      {selectionMode ? (
        // Same height as the pagination bar, so selecting never changes how many cards fit.
        <div className="flex h-8 min-w-0 items-center gap-1.5 overflow-x-auto rounded-md bg-primary/5 px-2 text-[13px] ring-1 ring-primary/20" role="toolbar" aria-label="批量操作">
          <span className="shrink-0 font-medium tabular-nums">已选 {selected.size} 项</span>
          <span className="hidden shrink-0 text-xs text-muted-foreground xl:inline">Shift 点选范围 · Ctrl+A 全选本页 · Esc 退出</span>
          <span className="flex-1" />
          <Button size="sm" variant="outline" className="h-7 shrink-0 px-2.5 text-xs" disabled={!online || retryable.length === 0 || batchBusy !== null} onClick={() => void runBatchRetry()}>
            {batchBusy === "retry" && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
            继续 / 重试{retryable.length ? ` ${retryable.length}` : ""}
          </Button>
          {capabilities.archive_mutation && !platform.isNative && (
            <Button size="sm" variant="outline" className="h-7 shrink-0 px-2.5 text-xs" disabled={selected.size === 0 || batchBusy !== null} onClick={() => setBatchRetention(selectedItems)}>
              媒体保留…
            </Button>
          )}
          {!platform.isNative && (
            <Button size="sm" variant="outline" className="h-7 shrink-0 px-2.5 text-xs" disabled={selected.size === 0 || batchBusy !== null} onClick={() => void runBatchExport()} title="摘要、导图和字幕打包成 zip">
              {batchBusy === "export" && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              导出
            </Button>
          )}
          {capabilities.archive_mutation && (
            <Button size="sm" variant="outline" className="h-7 shrink-0 px-2.5 text-xs text-destructive hover:text-destructive" disabled={selected.size === 0 || batchBusy !== null} onClick={() => setConfirmBatchDelete(true)}>
              {batchBusy === "delete" && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              删除…
            </Button>
          )}
          <Button size="sm" variant="ghost" className="h-7 shrink-0 px-2.5 text-xs" onClick={() => setSelected(new Set(shown.map((item) => item.path)))}>全选本页</Button>
          <Button size="sm" variant="ghost" className="h-7 shrink-0 px-2.5 text-xs" onClick={exitSelection}>取消</Button>
        </div>
      ) : (
        <div className="relative flex h-8 min-w-0 items-center gap-1">
          <span className="w-40 shrink-0 truncate text-xs tabular-nums text-muted-foreground max-md:hidden">
            {filtered && facets?.total ? `筛选后 ${total} / 共 ${facets.total} 项` : `${facets?.total ?? total} 项`}
          </span>
          {(isPhone || totalPages <= 1) && <span className="flex-1" />}
          {!isPhone && totalPages > 1 && (
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
          {!platform.isNative && (
            <div className="flex min-w-40 shrink-0 items-center justify-end gap-1 max-md:min-w-0">
              {status === "failed" && online && total > 0 && (
                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => void openRetryAll()}>
                  全部重试…
                </Button>
              )}
              {status === "duplicates" && capabilities.archive_mutation && (
                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => void openCleanup()}>
                  清理失败副本…
                </Button>
              )}
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1.5 px-2 text-xs"
                onClick={() => setSelectionMode(true)}
                title="选择多个条目，批量重试或删除"
              >
                <HugeiconsIcon icon={CheckListIcon} className="size-4" />
                选择
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" size="icon-sm" variant="ghost" className="size-7" aria-label="文件库操作">
                    <HugeiconsIcon icon={MoreHorizontalIcon} className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-52">
                  <DropdownMenuItem disabled={checking || indexing} onClick={() => void checkFiles()}>
                    {checking || indexing ? <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" /> : null}
                    <span className="flex flex-col">
                      <span>{checking || indexing ? "正在检查文件…" : "检查文件"}</span>
                      <span className="text-xs text-muted-foreground">
                        {lastReconciledAt ? `上次检查 ${new Date(lastReconciledAt).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}` : "发现在 MPP 之外修改过的文件"}
                      </span>
                    </span>
                  </DropdownMenuItem>
                  {capabilities.archive_mutation && (
                    <DropdownMenuItem onClick={() => void openCleanup()}>清理失败副本…</DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>
      )}

      {retentionTarget && <MediaRetentionDialog archive={retentionTarget}
        onClose={() => setRetentionTarget(null)} onApplied={() => void refresh(true)} />}
      {batchRetention && <BatchMediaRetentionDialog archives={batchRetention}
        onClose={() => { setBatchRetention(null); exitSelection() }} onApplied={() => void refresh(true)} />}
      <AlertDialog open={retryAll !== null} onOpenChange={(open) => { if (!open && !retryAll?.running) setRetryAll(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重试 {retryAll?.items.length ?? 0} 个失败条目？</AlertDialogTitle>
            <AlertDialogDescription>
              每一项都从断点继续，已完成的步骤不重做。它们会依次排队，GPU 一次只处理一个。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-t-0">
            <AlertDialogCancel disabled={retryAll?.running}>取消</AlertDialogCancel>
            <AlertDialogAction disabled={retryAll?.running} onClick={(event) => { event.preventDefault(); void runRetryAll() }}>
              {retryAll?.running && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              {retryAll?.running ? `正在排队 ${retryAll.done} / ${retryAll.items.length}` : "全部重试"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
      <AlertDialog open={confirmBatchDelete} onOpenChange={setConfirmBatchDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除 {selected.size} 项？</AlertDialogTitle>
            <AlertDialogDescription>
              会删除这些条目的所有文件（归档、字幕、摘要等）；正在处理的任务会先停止。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-t-0">
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void runBatchDelete()}>删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={cleanup !== null} onOpenChange={(open) => { if (!open && !cleanup?.busy) setCleanup(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清理 {cleanup?.count ?? 0} 个失败副本？</AlertDialogTitle>
            <AlertDialogDescription>
              只删除「同一个来源已经有成功归档」的失败或已取消的副本，成功的那一份和仍在处理的都不会动。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-t-0">
            <AlertDialogCancel disabled={cleanup?.busy}>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={cleanup?.busy} onClick={() => void runCleanup()}>
              {cleanup?.busy && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              清理
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
