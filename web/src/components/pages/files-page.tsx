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
import { hasActiveFilters, type LibraryFilters } from "@/lib/archive-filters"
import { archiveQueryFilters, type LibraryScope } from "@/hooks/use-library-controls"
import { publishLibraryFacets } from "@/lib/library-facets"
import type { ArchiveItem } from "@/hooks/use-archives"
import { ArchiveCard } from "@/components/archive-card"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { MediaRetentionDialog } from "@/components/media-retention-dialog"
import { LibraryBatchActions } from "@/components/library/library-batch-actions"
import { FolderCard } from "@/components/library/folder-card"
import type { LibraryFolder } from "@/lib/library-types"
import { LibraryDropTarget } from "@/components/library/library-drop-target"
import { MoveContentsDialog } from "@/components/library/move-contents-dialog"
import { useLibraryMove } from "@/hooks/use-library-move"
import { startLibraryDrag } from "@/lib/library-drag"
import { libraryChanged } from "@/hooks/use-library-directory"
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
const EMPTY_FOLDERS: LibraryFolder[] = []

interface FilesPageProps {
  /** Search, status, type, source and sort from the title bar */
  /** "all" lists every item regardless of folders; folders and their contents otherwise */
  scope?: LibraryScope
  folderId?: string
  folders?: LibraryFolder[]
  onFolderOpen?: (id: string) => void
  onArchiveOpen?: (archive: ArchiveItem) => void
  filters: LibraryFilters
  /** Current page, kept in the URL so 返回 / reload restore it */
  page?: number
  onPageChange?: (page: number) => void
}

// Archive opened from the grid; focused again when the library remounts after 返回.
let returnFocusPath: string | null = null
// External-edit check runs automatically at most once per app session.
let autoCheckDone = false

export function FilesPage({
  filters, page: pageProp, onPageChange, scope = "folders", folderId, folders = [], onFolderOpen, onArchiveOpen,
}: FilesPageProps) {
  const all = scope === "all"
  const { capabilities, online } = useAppAccess()
  const platform = usePlatform()
  const isPhone = useMediaQuery("(max-width: 767px)")
  const { update: updatePrefs } = usePreferences()
  // Fallback local page state for embeddings that don't route the page through the URL (tests).
  const filterKey = JSON.stringify({ ...filters, folderId, scope })
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
  const limit = isPhone ? phoneLimit : pageSize
  const folderScope = all ? {} : folderId ? { folder_id: folderId } : { unclassified: true, include_folders: true }
  const query = { page: isPhone ? 1 : page, page_size: limit, ...folderScope }
  const { archives, folders: pageFolders = EMPTY_FOLDERS, entries, total, archiveTotal = total, loading, error, indexing, lastReconciledAt, facets,
    refresh, removeArchive } = useArchivePage({ ...query, ...archiveQueryFilters(filters) })
  const [checking, setChecking] = useState(false)
  const [paginationRangeSize, setPaginationRangeSize] = useState(7)
  const [deleteTarget, setDeleteTarget] = useState<{ title: string; path: string; taskId?: string; taskDelete?: boolean } | null>(null)
  const [retentionTarget, setRetentionTarget] = useState<{ title: string; path: string } | null>(null)
  const [rerunningPath, setRerunningPath] = useState<string | null>(null)
  const [checkpointRerunningPath, setCheckpointRerunningPath] = useState<string | null>(null)
  const [taskActionPath, setTaskActionPath] = useState<string | null>(null)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [moveKeys, setMoveKeys] = useState<string[] | null>(null)
  const [dragging, setDragging] = useState(false)
  const moved = (keys: string[]) => {
    setSelected((current) => new Set([...current].filter((key) => !keys.includes(key))))
    setMoveKeys((current) => current?.filter((key) => !keys.includes(key)) ?? null)
    removeArchive()
  }
  const { busy: moving, move } = useLibraryMove(moved)
  const canMove = online && capabilities.archive_mutation
  const folderNames = useMemo(() => new Map(folders.map((folder) => [folder.id, folder.name])), [folders])
  const coverKey = folders.find((folder) => folder.id === folderId)?.cover_content_key ?? null
  const setCover = async (key: string) => {
    if (!folderId) return
    try {
      await api.library.setCover(folderId, key)
      libraryChanged()
      notifySuccess("已设为文件夹封面")
    } catch (reason) {
      notifyError("设置封面失败", reason)
    }
  }
  const anchorIndex = useRef<number | null>(null)
  const [retryAll, setRetryAll] = useState<{ items: ArchiveItem[]; done: number; running: boolean } | null>(null)
  const [cleanup, setCleanup] = useState<{ count: number; busy: boolean } | null>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const [lastPage, setLastPage] = useState({ filterKey, archives, folders: pageFolders, entries })
  if (isPhone && !loading && !error && (lastPage.filterKey !== filterKey || lastPage.archives !== archives || lastPage.folders !== pageFolders || lastPage.entries !== entries)) {
    setLastPage({ filterKey, archives, folders: pageFolders, entries })
  }
  // On phones "load more" grows one query; keep showing what we had while it reloads.
  const held = loading && isPhone && lastPage.filterKey === filterKey ? lastPage : null
  const shown = held?.archives ?? archives
  const shownFolders = held?.folders ?? pageFolders
  const shownEntries = held?.entries ?? entries
  const archiveMap = new Map(shown.map((item) => [item.archive_id ?? item.path, item]))
  const folderMap = new Map(shownFolders.map((folder) => [folder.id, folder]))
  const visibleEntries = shownEntries ?? [
    ...shown.map((item) => ({ kind: "archive" as const, id: item.archive_id ?? item.path })),
    ...shownFolders.map((folder) => ({ kind: "folder" as const, id: folder.id })),
  ]
  const itemKey = (item: ArchiveItem) => item.content_key || item.path

  const { tasks: activeTasks } = useActiveTasks()
  const tasksById = useMemo(() => new Map<string, Task>(activeTasks.map((task) => [task.id, task])), [activeTasks])

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
  const safePage = Math.min(page, totalPages)
  useEffect(() => {
    if (!loading && !isPhone && page > totalPages) setPage(totalPages)
    anchorIndex.current = null
  }, [loading, isPhone, page, totalPages, setPage])

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
  }, [archives.length, pageFolders.length, total, loading, isPhone])

  // Phones scroll through the whole library instead of paging.
  useEffect(() => {
    const sentinel = sentinelRef.current
    const grid = gridRef.current
    if (!isPhone || !sentinel || !grid || typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loading && shown.length + pageFolders.length < total && phoneLimit < MAX_QUERY) {
        setPhoneState({ filterKey, limit: phoneLimit + PHONE_BATCH })
      }
    }, { root: grid, rootMargin: "400px" })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [filterKey, isPhone, loading, phoneLimit, shown.length, pageFolders.length, total])

  // ---------- selection ----------
  const exitSelection = useCallback(() => {
    setSelectionMode(false)
    setSelected(new Set())
    anchorIndex.current = null
  }, [])

  useEffect(() => {
    exitSelection()
    setMoveKeys(null)
    setDragging(false)
  }, [filterKey, exitSelection])

  const toggleSelect = (index: number, event: ReactMouseEvent) => {
    const path = shown[index] ? itemKey(shown[index]) : null
    if (!path) return
    setSelectionMode(true)
    setSelected((current) => {
      const next = new Set(current)
      if (event.shiftKey && anchorIndex.current !== null) {
        const [from, to] = [anchorIndex.current, index].sort((a, b) => a - b)
        for (let i = from; i <= to; i += 1) next.add(itemKey(shown[i]))
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
        setSelected((current) => new Set([...current, ...shown.map(itemKey)]))
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [exitSelection, selectionMode, shown])

  // ---------- retry every failed item in the current filter ----------
  const openRetryAll = async () => {
    try {
      const found: ArchiveItem[] = []
      for (let next = 1; ; next += 1) {
        const result = await api.archives.page({
          page: next, page_size: MAX_QUERY, ...archiveQueryFilters({ ...filters, statuses: ["failed"], duplicates: false }),
          ...(all ? {} : folderId ? { folder_id: folderId } : { unclassified: true }),
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
    returnFocusPath = path
    const item = shown.find((archive) => archive.path === path)
    if (onArchiveOpen && item) { onArchiveOpen(item); return }
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
  const filtered = hasActiveFilters(filters)

  if (loading && !retentionTarget && shown.length === 0 && pageFolders.length === 0) {
    return <LoadingState title="正在加载文件" className="h-full" />
  }

  return (
    <div className={cn(
      "relative grid h-full min-h-0 gap-2 px-3 pt-3 pb-1 sm:px-4",
      platform.isNative ? "grid-rows-[auto_minmax(0,1fr)_auto]" : "grid-rows-[minmax(0,1fr)_auto]",
    )}>
      {platform.isNative && <OfflineSyncStatus compact />}
      {dragging && (folderId || all) && <div className="absolute inset-x-4 top-4 z-20 flex max-h-48 flex-wrap items-center gap-2 overflow-auto rounded-md border bg-background/95 p-2 shadow-md" aria-label="拖动内容以移动">
        <span className="text-xs text-muted-foreground">移到：</span>
        <LibraryDropTarget label="未分类" disabled={moving} onDropContents={(keys) => void move(keys, null)}><span className="block rounded-md border px-3 py-2 text-xs">未分类</span></LibraryDropTarget>
        {folders.filter((folder) => folder.id !== folderId).map((folder) => <LibraryDropTarget key={folder.id} label={folder.name} disabled={moving} onDropContents={(keys) => void move(keys, folder.id)}><span className="block max-w-64 truncate rounded-md border px-3 py-2 text-xs">{folder.name}</span></LibraryDropTarget>)}
      </div>}

      {/* Grid */}
      {shown.length > 0 || shownFolders.length > 0 ? (
        <div
          ref={gridRef}
          data-testid="archive-grid"
          className="grid h-full min-h-0 grid-cols-2 content-start items-start gap-3 overflow-x-hidden overflow-y-auto max-md:*:[contain-intrinsic-size:auto_220px] max-md:*:[content-visibility:auto] sm:gap-x-5 sm:gap-y-4 lg:grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] min-[1972px]:grid-cols-7"
          style={gridLayout ? { gridTemplateColumns: `repeat(${gridLayout.columns}, minmax(0, 1fr))`, rowGap: gridLayout.rowGap } : undefined}
        >
          {visibleEntries.map((entry) => {
            if (entry.kind === "folder") {
              const folder = folderMap.get(entry.id)
              return folder ? <LibraryDropTarget key={`folder:${folder.id}`} label={folder.name} disabled={moving} onDropContents={(keys) => void move(keys, folder.id)}>
                <FolderCard folder={folder} onOpen={() => onFolderOpen?.(folder.id)} />
              </LibraryDropTarget> : null
            }
            const a = archiveMap.get(entry.id)
            if (!a) return null
            const index = shown.indexOf(a)
            return (
            <ArchiveCard
              key={a.path}
              archive={a}
              compact
              task={a.task_id ? tasksById.get(a.task_id) : undefined}
              selectionMode={selectionMode}
              selected={selected.has(itemKey(a))}
              onSelect={(event) => toggleSelect(index, event)}
              draggable={canMove && !!a.content_key && !moving}
              onDragStart={(event) => { startLibraryDrag(event, selected.has(itemKey(a)) ? [...selected] : [itemKey(a)]); setDragging(true) }}
              onDragEnd={() => setDragging(false)}
              onMove={canMove && a.content_key ? () => setMoveKeys([a.content_key!]) : undefined}
              onMoveOut={canMove && a.content_key && (folderId || (all && a.folder_id)) ? () => void move([a.content_key!], null) : undefined}
              folderName={all && a.folder_id ? folderNames.get(a.folder_id) : undefined}
              onSetCover={canMove && folderId && a.content_key ? () => void setCover(a.content_key!) : undefined}
              isCover={Boolean(folderId && a.content_key && coverKey === a.content_key)}
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
          )})}
          {isPhone && (
            <div ref={sentinelRef} className="col-span-full flex min-h-10 items-center justify-center py-2 text-xs text-muted-foreground">
              {shown.length + shownFolders.length < total
                ? (loading ? "正在加载…" : phoneLimit >= MAX_QUERY ? `已显示前 ${shown.length + shownFolders.length} 项，用搜索或筛选缩小范围` : `已显示 ${shown.length + shownFolders.length} / ${total}`)
                : `共 ${total} 项`}
            </div>
          )}
        </div>
      ) : (
        <EmptyState
          className="h-full min-h-0"
          title={error ? "文件加载失败" : filtered ? "没有匹配的结果" : folderId ? "文件夹是空的" : "还没有归档结果"}
          description={error ?? (filtered ? "调整搜索条件或筛选项后再试。" : folderId ? "选中内容后用“移到文件夹…”，或把卡片拖到文件夹上。" : "处理完成后，文件会显示在这里。")}
        />
      )}

      {/* Selection actions replace pagination while selecting */}
      {selectionMode ? (
        // Same height as the pagination bar, so selecting never changes how many cards fit.
        <div className="flex h-8 min-w-0 items-center gap-1.5 overflow-x-auto rounded-md bg-primary/5 px-2 text-[13px] ring-1 ring-primary/20" role="toolbar" aria-label="批量操作">
          <span className="shrink-0 font-medium tabular-nums">已选 {selected.size} 项</span>
          <span className="hidden shrink-0 text-xs text-muted-foreground xl:inline">Shift 点选范围 · Ctrl+A 全选本页 · Esc 退出</span>
          <span className="flex-1" />
          {canMove && <><Button size="sm" variant="outline" className="h-7 shrink-0 px-2 text-xs" disabled={!selected.size || moving} onClick={() => setMoveKeys([...selected])}>移到文件夹…</Button>{(folderId || all) && <Button size="sm" variant="outline" className="h-7 shrink-0 px-2 text-xs" disabled={!selected.size || moving} onClick={() => void move([...selected], null)}>移到未分类</Button>}</>}
          <LibraryBatchActions scope={{ type: "selected", content_keys: [...selected] }} compact onCompleted={(keys) => {
            setSelected((current) => new Set([...current].filter((key) => !keys.includes(key))))
            removeArchive()
          }} />
          <Button size="sm" variant="ghost" className="h-7 shrink-0 px-2.5 text-xs" onClick={() => setSelected((current) => new Set([...current, ...shown.map(itemKey)]))}>全选本页</Button>
          <Button size="sm" variant="ghost" className="h-7 shrink-0 px-2.5 text-xs" onClick={exitSelection}>取消</Button>
          {!isPhone && totalPages > 1 && <><Button size="sm" variant="ghost" className="h-7 shrink-0 px-2 text-xs" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>上一页</Button><span className="shrink-0 text-xs">{safePage} / {totalPages}</span><Button size="sm" variant="ghost" className="h-7 shrink-0 px-2 text-xs" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>下一页</Button></>}
        </div>
      ) : (
        <div className="relative flex h-8 min-w-0 items-center gap-1">
          <span className="w-40 shrink-0 truncate text-xs tabular-nums text-muted-foreground max-md:hidden">
            {filtered && facets?.total ? `筛选后 ${total} / 共 ${facets.total} 项` : `${total} 项`}
          </span>
          {(isPhone || totalPages <= 1) && <span className="flex-1" />}
          {platform.isNative && canMove && <Button type="button" size="sm" variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={() => setSelectionMode(true)}>选择</Button>}
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
              {filters.statuses.includes("failed") && !filters.duplicates && online && archiveTotal > 0 && (
                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => void openRetryAll()}>
                  全部重试…
                </Button>
              )}
              {filters.duplicates && capabilities.archive_mutation && (
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
      {moveKeys?.length ? <MoveContentsDialog contentKeys={moveKeys} sourceFolderId={folderId} onClose={() => setMoveKeys(null)} onMoved={moved} /> : null}
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
